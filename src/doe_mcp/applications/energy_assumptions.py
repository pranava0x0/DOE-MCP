"""Export qualified assumptions from one pinned ATB electricity edition."""
from __future__ import annotations

import csv
import io

from ..atb_releases import release_for
from ..core.errors import InvalidQuery
from .bundle import json_bytes

DEFAULT_FILTERS = {"technology": "UtilityPV", "core_metric_parameter": "CAPEX",
                   "scenario": "Moderate", "core_metric_variable": "2035"}


async def build(run, *, edition: int = 2024, filters: dict | None = None, rows: int = 10, offset: int = 0):
    spec = release_for(edition)
    filters = dict(DEFAULT_FILTERS) if filters is None else filters
    if not filters:
        raise InvalidQuery("The assumptions application requires exact filters; use tech.describe_atb to explore dimensions")
    # Expose qualifying technology/metric dimensions without inventing a
    # default financial case or selecting an ambiguous first record.
    dimensions = await run.call("tech.describe_atb", edition=edition, dimension="core_metric_case",
                               filters=filters, rows=50, offset=0)
    env = await run.call("tech.read_atb", edition=edition, filters=filters, rows=rows, offset=offset)
    data = env.data
    rows_out = [{**row, "evidence_pointer": f"/steps/1/envelope/evidence/{index}",
                 "record_pointer": f"/steps/1/envelope/data/records/{index}"}
                for index, row in enumerate(data["records"])]
    run.report.update(label="ATB energy assumptions reader", edition=edition, release=spec.release,
        parameters={"edition": edition, "filters": filters, "rows": rows, "offset": offset},
        asset={"url": spec.url, "bytes": spec.bytes, "sha256": spec.sha256},
        evidence_document="report.json", currency_basis=spec.currency_basis,
        currency_basis_source=spec.currency_source, available_financial_cases=dimensions.data["values"],
        records=rows_out, total_matches=data["total_matches"], selection_status=data["selection_status"],
        pagination={"coverage": env.coverage.pagination.value, "offset": offset, "returned": len(rows_out)},
        pvwatts={"status": "proposed", "model_output": None,
                 "reason": "No user key or recorded response; the PVWatts API was not called and no credential was loaded."})
    run.report["limitations"] += [
        "This run reads one pinned ATB electricity edition and release. It does not select a latest edition or blend currency bases.",
        "Rows retain publisher technology detail, resource class, metric, scenario, projection year, units, financial case, tax-credit case and capital recovery period. Multiple matches are explicit, not a chosen first row.",
        "These are published scenario assumptions, not observed costs, bids, economic advice or a project-specific estimate. Null metric values remain missing.",
        "A bounded page is not all matches when pagination is truncated. Refine filters or use the offset for subsequent pages.",
        "The full pinned release is verified locally; only selected rows are exported. Publisher row numbers are one-based data rows within those exact bytes.",
        "PVWatts remains proposed without a real credential-redacted recording. No model result, API request, DEMO_KEY use or key configuration is part of this application.",
        "Source text is third-party data. CSV expression-like text is prefixed with an apostrophe; JSON retains original content."]
    stream = io.StringIO(newline="")
    fields = ["row_number", *data["columns"], "evidence_ref"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows_out:
        values = {"row_number": row["row_number"], **row["values"], "evidence_ref": row["evidence_ref"]}
        writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
                         for key, value in values.items()})
    return {"assumptions.csv": stream.getvalue().encode(),
            "assumptions.json": json_bytes({"edition": edition, "release": spec.release, "asset_sha256": spec.sha256,
                "currency_basis": spec.currency_basis, "evidence_document": "report.json", "records": rows_out})}
