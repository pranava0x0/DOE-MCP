"""Genesis method examples over complete filtered tables from the GCAM release."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .core.errors import SourceSchemaChanged, SourceUnavailable


@dataclass(frozen=True)
class Walk:
    title: str
    question: str
    skill: str
    method: str
    tool: str
    filters: dict[str, str]
    limit: str


WALKS = {
    "latin-america-regions": Walk(
        "Map Latin American country codes", "Does the legacy Latin America label identify a single numeric GCAM region?",
        "genesis-audit-region-keys", "data-exploration", "economy.country_regions", {"region_GCAM3": "Latin America"},
        "Country and territory codes belong to this model release. They do not define political boundaries."),
    "china-cooling-balance": Walk(
        "Check China's coal cooling shares", "Do the published cooling shares add to one in both 2010 and 2020?",
        "genesis-check-share-balance", "data-exploration", "water.cooling_shares", {"region_GCAM3": "China", "plant_type": "coal"},
        "These are unitless model inputs for the legacy China region. They do not measure water use or plant counts."),
    "world-bioenergy-columns": Walk(
        "Compare regional bioenergy settings", "Where does the alternative column differ from the preference GCAM uses?",
        "genesis-document-model-columns", "datacard-generator", "land.bioenergy_preferences", {},
        "The publisher says only the preference column is used. A difference does not establish a policy change or a scenario result."),
    "world-geothermal-grades": Walk(
        "Compare geothermal heat across regions", "How do the regions compare for the same hydrothermal resource grade?",
        "genesis-compare-resource-grades", "data-exploration", "power.geothermal_curves",
        {"resource": "geothermal", "subresource": "hydrothermal", "grade": "grade 2"},
        "These inputs are heat available to power plants, in thermal EJ per year. They do not measure generated electricity."),
}


def _number(value):
    try:
        n = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise SourceSchemaChanged("GCAM worksheet requires a numeric model input.") from err
    if not n.is_finite() or n < 0:
        raise SourceSchemaChanged("GCAM worksheet requires a finite nonnegative model input.")
    return n


def _rows(env, columns, key):
    data = env.data
    records = data["records"]
    if not records or data["offset"] != 0 or len(records) != data["total_matches"]:
        raise SourceUnavailable("Global worksheet needs the whole filtered table; no partial result was scored.")
    seen = set()
    out = []
    for r in records:
        v = r["values"]
        if any(v.get(k) in (None, "") for k in columns):
            raise SourceSchemaChanged("GCAM worksheet has a blank required field.")
        identity = tuple(v[k] for k in key)
        if identity in seen:
            raise SourceSchemaChanged("GCAM worksheet has a duplicate row key.")
        seen.add(identity)
        out.append([*(v[k] for k in columns), r["evidence_ref"]])
    return out


def make_sheet(case, env):
    if case == "latin-america-regions":
        cols = ["iso", "country_name", "region_GCAM3", "GCAM_region_ID"]
        rows = _rows(env, cols, ["iso"])
        stats = {"codes": len(rows), "numeric_regions": len({r[3] for r in rows})}
        answer = f"{stats['codes']} country and territory codes share the legacy label but map to {stats['numeric_regions']} numeric region IDs."
    elif case == "china-cooling-balance":
        cols = ["cooling_system", "water_type", "2010", "2020"]
        rows = _rows(env, cols, cols[:2])
        totals = [Decimal(0), Decimal(0)]
        for row in rows:
            before, after = map(_number, row[2:4])
            if max(before, after) > 1:
                raise SourceSchemaChanged("A GCAM cooling share exceeds one.")
            totals[0] += before
            totals[1] += after
            row.insert(-1, float(after - before))
        cols.append("Change in share (2020 minus 2010)")
        stats = dict(zip(("sum_2010", "sum_2020"), (str(s) for s in totals), strict=True))
        answer = f"Across {len(rows)} cooling and water categories the shares sum to {totals[0]} in 2010 and {totals[1]} in 2020."
    elif case == "world-bioenergy-columns":
        if not any("only the preference column is used" in s.lower() for s in env.data["source_notes"]):
            raise SourceSchemaChanged("GCAM no longer documents which preference column it uses.")
        cols = ["GCAM_region_ID", "region", "preference", "preference_alternative"]
        rows = _rows(env, cols, ["GCAM_region_ID"])
        for r in rows:
            r.insert(-1, r[2] != r[3])
        cols.append("Columns differ")
        stats = {"regions": len(rows), "different": sum(r[4] for r in rows)}
        answer = f"The two columns differ in {stats['different']} of {len(rows)} regional rows. Only preference is used."
    elif case == "world-geothermal-grades":
        cols = ["region_GCAM3", "grade", "available", "extractioncost"]
        rows = _rows(env, cols, ["region_GCAM3"])
        rows.sort(key=lambda r: (-_number(r[2]), r[0]))
        cols = ["Legacy region", "Grade", "Available heat (thermal EJ/year)", "Extraction cost (1975 USD/GJ)"]
        stats = {"regions": len(rows), "maximum": rows[0][2], "minimum": rows[-1][2]}
        answer = f"Across {len(rows)} legacy regions, grade 2 heat inputs range from {stats['minimum']} to {stats['maximum']} thermal EJ per year."
    else:
        raise ValueError(f"Unknown global worksheet: {case}")
    return {"caption": WALKS[case].title, "columns": [*cols, "Step 1 evidence ID"], "rows": rows, "summary": stats}, answer
