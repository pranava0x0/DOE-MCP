"""Bounded research discovery with evidence, identity and asserted links separated."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from urllib.parse import quote

from ..adapters.osti_family import normalized_doi
from ..core.errors import InvalidQuery, SourceSchemaChanged
from .bundle import json_bytes

PRESET = {"literature": "perovskite solar", "dataset": "geothermal", "software": "machine learning"}
SEARCHES = {"literature": ("research.search_literature", "records", 3),
            "dataset": ("research.search_datasets", "datasets", 2),
            "software": ("research.search_software", "software", 3)}


def receipt(records: list[dict]) -> dict:
    """Match only exact normalized DOI keys, retaining unresolved publisher claims."""
    by_doi: dict[str, list[str]] = {}
    for record in records:
        if record["doi_key"]:
            by_doi.setdefault(record["doi_key"], []).append(record["node_id"])
    identities = [{"doi": key, "members": sorted(set(members)),
                   "evidence": [e for r in records if r["doi_key"] == key for e in r["evidence"]]}
                  for key, members in sorted(by_doi.items()) if len(set(members)) > 1]
    claims: dict[str, dict] = {}
    for record in records:
        for assertion in record["assertions"]:
            key = json.dumps([record["node_id"], assertion["relationship"],
                              assertion["target_namespace"], assertion["target_identifier"]])
            claim = claims.setdefault(key, {
                "edge_id": "relation:" + hashlib.sha256(key.encode()).hexdigest()[:24],
                "from": record["node_id"], "relationship": assertion["relationship"],
                "target_namespace": assertion["target_namespace"],
                "target_identifier": assertion["target_identifier"],
                "matched_nodes": sorted(set(by_doi.get(assertion["target_identifier"], [])))
                    if assertion["target_namespace"] == "doi" else [],
                "evidence": []})
            if assertion["evidence"] not in claim["evidence"]:
                claim["evidence"].append(assertion["evidence"])
    edges = sorted(claims.values(), key=lambda row: row["edge_id"])
    matched = {node for group in identities for node in group["members"]}
    for edge in edges:
        edge["resolution"] = "matched_returned_record" if edge["matched_nodes"] else "target_not_in_returned_records"
        if edge["matched_nodes"]:
            matched.update([edge["from"], *edge["matched_nodes"]])
    return {"shared_identity": identities, "explicit_relationships": edges,
            "unmatched_records": [{"node_id": row["node_id"],
                "reason": "no_resolved_explicit_relationship_or_shared_doi_in_bounded_results"}
                for row in records if row["node_id"] not in matched]}


async def build(run, *, query: str = "", literature_query: str = "", dataset_query: str = "",
                software_query: str = "", rows: int | None = None, page_budget: int = 1):
    if not 1 <= page_budget <= 5 or (rows is not None and not 1 <= rows <= 50):
        raise InvalidQuery("page-budget must be 1–5 and rows must be 1–50")
    overrides = {"literature": literature_query, "dataset": dataset_query, "software": software_query}
    queries = {kind: overrides[kind].strip() or query.strip() or PRESET[kind] for kind in SEARCHES}
    records: dict[str, dict] = {}
    scopes = []
    for kind, (tool, result_field, default_rows) in SEARCHES.items():
        page_scopes = []
        repeated = 0
        for page in range(1, page_budget + 1):
            env = await run.call(tool, query=queries[kind], rows=rows or default_rows, page=page, detailed=True)
            step = len(run.report["steps"]) - 1
            envelope = run.report["steps"][step]["envelope"]
            sources = {source["id"]: source["source_id"] for source in envelope["provenance"]}
            page_scopes.append({"page": page, "step": step, "coverage": envelope["coverage"],
                                "publisher_totals": env.data.get("per_source", env.data.get("total_matches"))})
            for index, row in enumerate(env.data[result_field]):
                source = row.get("source_id")
                if not source or not row.get("id"):
                    raise SourceSchemaChanged("research record has no source-scoped identifier")
                pointers = [{"step": step, "record_pointer": f"/steps/{step}/envelope/data/{result_field}/{index}",
                             "evidence_pointer": f"/steps/{step}/envelope/evidence/{position}",
                             "evidence_id": item["id"], "source_id": source, "locator": item.get("locator")}
                            for position, item in enumerate(envelope["evidence"])
                            if item["record_id"] == row["id"] and sources.get(item["source_ref"]) == source]
                if not pointers:
                    raise SourceSchemaChanged("research record has no matching source evidence")
                node = source + ":" + quote(row["id"], safe="")
                repeated += int(node in records)
                record = records.setdefault(node, {"node_id": node, "collection": kind, "source_id": source,
                    "record_id": row["id"], "title": row["title"], "doi": row.get("doi"),
                    "doi_key": normalized_doi(row.get("doi")), "repository": row.get("repository"),
                    "evidence": [], "assertions": [], "unparsed_relationship_count": 0})
                if record["doi_key"] != normalized_doi(row.get("doi")):
                    raise SourceSchemaChanged("research record DOI changed during bounded pagination")
                record["evidence"].extend(pointers)
                related = row.get("related_artifacts", {})
                record["unparsed_relationship_count"] = max(record["unparsed_relationship_count"], related.get("unparsed_count", 0))
                for position, assertion in enumerate(related.get("assertions", [])):
                    record["assertions"].append({**assertion, "evidence": {**pointers[0],
                        "assertion_pointer": pointers[0]["record_pointer"] + f"/related_artifacts/assertions/{position}",
                        "origin_field": assertion["origin_field"], "publisher_value": assertion["publisher_value"]}})
            if env.coverage.pagination.value == "complete":
                break
        totals = [json.dumps(scope["publisher_totals"], sort_keys=True) for scope in page_scopes]
        gaps = (["records_repeated_across_pages"] if repeated else []) + (["publisher_totals_changed"] if len(set(totals)) > 1 else [])
        scopes.append({"collection": kind, "query": queries[kind], "rows_per_source_per_page": rows or default_rows,
                       "pages": page_scopes, "pagination_complete": page_scopes[-1]["coverage"]["pagination"] == "complete" and not gaps,
                       "consistency_gaps": gaps, "repeated_records": repeated,
                       "stop_reason": "publisher_page_complete" if env.coverage.pagination.value == "complete" else "page_budget_exhausted"})
    inventory = sorted(records.values(), key=lambda record: record["node_id"])
    linkage = receipt(inventory)
    run.report.update(label="Research artifact linkage explorer", evidence_document="report.json",
                      parameters={"queries": queries, "rows": rows, "page_budget": page_budget},
                      records=inventory, search_scope=scopes, **linkage)
    run.report["limitations"] += [
        "Each collection is searched independently. The recorded preset uses different recorded topics; co-returned records are not relationship evidence.",
        "Only exact normalized DOIs establish shared identity. Shared identity does not assert that a paper uses a dataset or software.",
        "Explicit links require a typed DOI assertion in the publisher related_identifiers field. Grants, awards, journal prose, titles and authors never create links. Unparsed relationship values are counted.",
        "Unresolved targets remain publisher assertions, without invented metadata, URLs or follow-up requests. No target artifact or repository content is downloaded.",
        "Search scope retains every page's coverage and publisher totals. Unmatched means unmatched within these bounded returned records, not proof that a relationship does not exist.",
        "The literature tool deduplicates its two collections by DOI; this inventory preserves its returned rows and original envelope evidence, rather than reconstructing discarded rows.",
        "Source text is third-party data. CSV cells that could be spreadsheet expressions are prefixed with an apostrophe; JSON retains original titles."]
    output = io.StringIO(newline="")
    fields = ["node_id", "collection", "source_id", "record_id", "title", "doi", "repository"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in inventory:
        writer.writerow({key: ("'" + value if isinstance(value := record.get(key), str) and value.lstrip().startswith(("=", "+", "-", "@")) else value) for key in fields})
    return {"inventory.csv": output.getvalue().encode(),
            "linkage.json": json_bytes({"evidence_document": "report.json", "records": inventory, **linkage})}
