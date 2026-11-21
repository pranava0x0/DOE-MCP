"""Track Federal Register notice metadata; docket comments are outside scope."""
from __future__ import annotations

from datetime import date

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..demo_worksheets import csv_text
from .bundle import json_bytes

DEFAULT_DOCKET = "EERE-2025-OT-0031"


def _identity(record):
    return record.get("id") or record.get("document_number")


def compare(current: dict, previous: dict) -> dict:
    if not isinstance(previous, dict) or previous.get("application") != "docket-tracker" or previous.get("parameters") != current["parameters"] or previous.get("mode") != current["mode"]:
        raise InvalidQuery("previous snapshot must use the same application, mode and search parameters")
    if not isinstance(previous.get("documents"), list) or any(not isinstance(d, dict) or not _identity(d) for d in previous["documents"]):
        raise InvalidQuery("previous snapshot has no valid document records")
    if not isinstance(previous.get("pagination"), dict) or type(previous["pagination"].get("complete")) is not bool:
        raise InvalidQuery("previous snapshot has no pagination completeness")
    old = {_identity(d): d for d in previous["documents"]}
    new = {_identity(d): d for d in current["documents"]}
    return {"added": sorted(new.keys() - old.keys()),
            "changed": sorted(k for k in new.keys() & old.keys() if new[k] != old[k]),
            "not_returned": sorted(old.keys() - new.keys()),
            "absence_note": "Not returned in this snapshot does not mean deleted, withdrawn or legally ineffective.",
            "both_searches_complete": current["pagination"]["complete"] and previous["pagination"]["complete"]}


async def build(run, *, query: str, docket: str, since: str, until: str, document_type: str,
                rows: int, page_budget: int, previous: dict | None = None):
    if not 1 <= page_budget <= 50 or not 2 <= rows <= 100:
        raise InvalidQuery("page budget must be 1 through 50 and page size 2 through 100")
    for stamp in [since, until]:
        if stamp:
            try:
                date.fromisoformat(stamp)
            except ValueError as err:
                raise InvalidQuery("publication dates must use YYYY-MM-DD") from err
    if since and until and since > until:
        raise InvalidQuery("publication start must precede publication end")
    params = dict(query=query, docket=docket, since=since, until=until, document_type=document_type, rows=rows)
    documents, seen, pages, excluded, gaps = [], {}, [], 0, []
    complete = False
    totals = []
    for page in range(1, page_budget + 1):
        env = await run.call("docs.search_rulemakings", **params, page=page)
        data = env.data
        if data["page"] != page or data["page_size"] != rows:
            raise SourceSchemaChanged("Federal Register returned a different page or page size")
        pages.append(page)
        excluded += data.get("excluded_documents", 0)
        totals.append(data.get("total_matches"))
        for record in data["documents"]:
            identity = record["document_number"]
            if not identity:
                raise SourceSchemaChanged("Federal Register notice has no document number")
            if identity in seen:
                gaps.append("document_repeated_across_pages")
                continue
            seen[identity] = record
            documents.append(record)
        if env.coverage.pagination.value == "complete":
            complete = True
            break
        reachable = data.get("pages_reachable")
        if reachable and page >= reachable:
            gaps.append("publisher_reachable_page_limit")
            break
    else:
        gaps.append("page_budget_exhausted")
    if any(total is None for total in totals):
        gaps.append("unknown_total")
    if len(set(totals)) > 1:
        gaps.append("total_changed_during_pagination")
    groups = {}
    for record in documents:
        ids = record.get("docket_ids") or []
        if not isinstance(ids, list) or any(not isinstance(value, str) or not value.strip() for value in ids):
            raise SourceSchemaChanged("Federal Register returned malformed docket identifiers")
        for identity in dict.fromkeys(ids or [None]):
            groups.setdefault(identity, []).append(record["document_number"])
    run.report.update(parameters=params, page_budget=page_budget, documents=documents,
                      docket_groups=[{"docket_id": key, "documents": value} for key, value in groups.items()],
                      pagination={"pages_read": pages, "complete": complete and not gaps,
                                  "gaps": list(dict.fromkeys(gaps)), "publisher_totals": totals,
                                  "excluded_ferc_documents": excluded})
    if previous is not None:
        run.report["changes"] = compare(run.report, previous)
    run.report["limitations"] += ["Federal Register DOE notice metadata only; Regulations.gov docket comments and supporting materials were not retrieved.",
        "Publication, effective and comment-close dates are separate publisher fields; absent dates remain unknown.",
        "Search totals include FERC records excluded by the existing DOE source scope. Pagination continues through pages containing only excluded records.",
        "Multiple docket identifiers retain separate group memberships; records with no docket ID remain explicitly ungrouped.",
        "A snapshot records what the bounded search returned; absence does not establish deletion, withdrawal or current legal effect."]
    fields = ["document_number", "title", "type", "docket_ids", "publication_date", "effective_on", "comments_close_on", "html_url", "pdf_url", "full_text_url"]
    csv_rows = [[json_bytes(record.get(key)).decode().strip() if key == "docket_ids" else record.get(key) for key in fields] for record in documents]
    return {"notices.csv": csv_text(fields, csv_rows).encode()}


async def build_regulations(run, *, docket: str, agency_id: str, rows: int, page_budget: int,
                            query: str = "", since: str = "", until: str = "", previous: dict | None = None):
    """Read a docket and bounded document pages, preserving publisher links."""
    if not docket.strip() or not agency_id.strip() or not 5 <= rows <= 20 or not 1 <= page_budget <= 20:
        raise InvalidQuery("docket and agency are required; rows must be 5 through 20 and page budget 1 through 20")
    for stamp in [since, until]:
        if stamp:
            try:
                date.fromisoformat(stamp)
            except ValueError as err:
                raise InvalidQuery("posted date filters must use YYYY-MM-DD") from err
    if since and until and since > until:
        raise InvalidQuery("posted start date must precede posted end date")
    parameters = dict(source="regulations", docket=docket, agency_id=agency_id, rows=rows,
                      query=query, since=since, until=until)
    docket_env = await run.call("docs.get_docket", docket_id=docket)
    documents, seen, pages, totals, gaps = [], set(), [], [], []
    complete = False
    for page in range(1, page_budget + 1):
        env = await run.call("docs.search_docket_documents", docket_id=docket, agency_id=agency_id,
                             query=query, since=since, until=until, rows=rows, page=page)
        data = env.data
        if data["page"] != page:
            raise SourceSchemaChanged("Regulations.gov returned a different document page")
        pages.append(page)
        totals.append(data.get("total_matches"))
        for record in data["documents"]:
            identity = record.get("id")
            if not identity:
                raise SourceSchemaChanged("Regulations.gov document has no identifier")
            if identity in seen:
                gaps.append("document_repeated_across_pages")
                continue
            seen.add(identity)
            documents.append(record)
        if env.coverage.pagination.value == "complete":
            complete = True
            break
    else:
        gaps.append("page_budget_exhausted")
    if any(total is None for total in totals):
        gaps.append("unknown_total")
    if len(set(totals)) > 1:
        gaps.append("total_changed_during_pagination")
    run.report.update(parameters=parameters, page_budget=page_budget, docket=docket_env.data["docket"],
                      documents=documents,
                      docket_groups=[{"docket_id": docket, "documents": [d["id"] for d in documents]}],
                      pagination={"pages_read": pages, "complete": complete and not gaps,
                                  "gaps": list(dict.fromkeys(gaps)), "publisher_totals": totals})
    if previous is not None:
        run.report["changes"] = compare(run.report, previous)
    run.report["limitations"] += ["Regulations.gov docket and document metadata only. Comment bodies, attachments and supporting file contents were not downloaded.",
        "Posted, comment-start, comment-end and last-modified timestamps remain separate publisher fields; absent values remain unknown.",
        "Query text and posted-date bounds are sent as publisher search filters. Search results are bounded by the recorded page budget.",
        "An incomplete snapshot cannot establish a complete docket. Records not returned are never labelled deleted or withdrawn.",
        "Federal Register document numbers are retained as publisher-provided cross references; no document URL is constructed from them."]
    fields = ["id", "docket_id", "agency_id", "title", "document_type", "posted_date", "comment_start_date", "comment_end_date", "last_modified_date", "fr_document_number", "open_for_comment", "withdrawn"]
    return {"docket-documents.csv": csv_text(fields, [[r.get(k) for k in fields] for r in documents]).encode()}
