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
    "world-desalination-electricity": Walk(
        "Convert desalination electricity inputs", "How much electricity does the reverse osmosis input represent in kWh per cubic metre?",
        "genesis-convert-energy-units", "data-exploration", "water.desalination_inputs",
        {"technology": "reverse osmosis", "minicam.energy.input": "elect_td_ind"},
        "These are global model coefficients, not measurements at a desalination plant. The seawater input is a separate row and is not converted."),
    "world-coefficient-gaps": Walk(
        "Inspect blanks in emissions coefficients", "Which carbon monoxide coefficients are filled, blank or explicitly zero?",
        "genesis-audit-coefficient-gaps", "data-exploration", "emissions.technology_coefficients", {},
        "A blank is not zero. Exception fields are retained but not resolved; this file gives no physical unit for the coefficients."),
    "world-retirement-parameters": Walk(
        "Separate two retirement parameters", "Which technology-period rows report both a lifetime and a retirement half-life?",
        "genesis-separate-retirement-parameters", "datacard-generator", "power.generator_retirement", {},
        "Lifetime is the cohort maximum; half-life belongs to a particular retirement function. A blank half-life gives no retirement date or proof that the function runs."),

    "world-crop-rates": Walk(
        "Compare crop yield assumptions", "Which fallback yield improvement rates change between 2050 and 2095?",
        "genesis-compare-fallback-rates", "data-exploration", "land.crop_yield_growth", {},
        "GCAM uses fallback rates where regional crop estimates are unavailable and beyond 2050. These rates are not harvested yields."),
    "world-capture-residual": Walk(
        "Account for uncaptured carbon", "What portion of CO₂ remains uncaptured under each electricity technology assumption?",
        "genesis-compute-residual-shares", "data-exploration", "power.carbon_capture", {},
        "A fraction of a technology’s CO₂ stream gives neither tonnes emitted nor a lifecycle emissions estimate."),
    "world-industry-response": Walk(
        "Read industrial demand elasticity", "At which published output levels is industrial income elasticity zero or negative?",
        "genesis-read-signed-response", "data-exploration", "economy.industrial_elasticity", {},
        "The rows specify model parameters at discrete output levels. They do not report industrial demand or an observed income response."),
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


def _number(value, *, signed=False):
    try:
        n = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise SourceSchemaChanged("GCAM worksheet requires a numeric model input.") from err
    if not n.is_finite() or (not signed and n < 0):
        raise SourceSchemaChanged("GCAM worksheet requires a finite model input in the permitted range.")
    return n


def _rows(env, columns, key, *, nullable=()):
    data = env.data
    records = data["records"]
    if not records or data["offset"] != 0 or len(records) != data["total_matches"]:
        raise SourceUnavailable("Global worksheet needs the whole filtered table; no partial result was scored.")
    seen = set()
    out = []
    for r in records:
        v = r["values"]
        if any(k not in v or (k not in nullable and v[k] in (None, "")) for k in columns):
            raise SourceSchemaChanged("GCAM worksheet has a blank required field.")
        identity = tuple(v[k] for k in key)
        if identity in seen:
            raise SourceSchemaChanged("GCAM worksheet has a duplicate row key.")
        seen.add(identity)
        out.append([*(v[k] for k in columns), r["evidence_ref"]])
    return out


def make_sheet(case, env):
    stats: dict[str, int | str]
    if case == "world-desalination-electricity":
        years = ["1971", "2015", "2017", "2100"]
        records = _rows(env, ["technology", "minicam.energy.input", *years], ["technology", "minicam.energy.input"])
        if len(records) != 1 or records[0][:2] != ["reverse osmosis", "elect_td_ind"]:
            raise SourceUnavailable("The electricity conversion needs one reverse osmosis electricity row.")
        record = records[0]
        rows = [[year, record[i + 2], str(_number(record[i + 2]) / Decimal("0.0036")), record[-1]]
                for i, year in enumerate(years)]
        cols = ["Year", "Electricity (GJ/m³)", "Electricity (kWh/m³)"]
        stats = {"periods": len(rows), "gj_per_kwh": "0.0036"}
        answer = "Reverse osmosis electricity inputs: " + "; ".join(f"{r[0]}: {r[2]} kWh/m³" for r in rows) + "."
    elif case == "world-coefficient-gaps":
        cols = ["supplysector", "subsector", "stub.technology", "CO", "exception", "exception_tech"]
        rows = _rows(env, cols, cols[:3], nullable=cols[3:])
        counts = {"blank": 0, "zero": 0, "nonzero": 0}
        for r in rows:
            status = "blank" if r[3] is None else "zero" if _number(r[3]) == 0 else "nonzero"
            r.insert(-1, status)
            counts[status] += 1
        cols = ["Sector", "Subsector", "Technology", "CO coefficient", "Exception species", "Exception technology", "CO cell"]
        stats = {"rows": len(rows), **counts}
        answer = (f"Among {len(rows)} technology rows, {counts['blank']} CO coefficients are blank, "
                  f"{counts['zero']} are explicitly zero, and {counts['nonzero']} are nonzero.")
    elif case == "world-retirement-parameters":
        notes = " ".join(env.data["source_notes"])
        if "lifetime: maximum lifetime of cohort" not in notes or "s-curve-shutdown-decider" not in notes:
            raise SourceSchemaChanged("The retirement table no longer explains the two parameter roles.")
        cols = ["subsector", "technology", "year", "lifetime", "half.life"]
        rows = _rows(env, cols, cols[:3], nullable=["half.life"])
        for r in rows:
            _number(r[3])
            if r[4] is not None:
                _number(r[4])
        both = sum(r[4] is not None for r in rows)
        stats = {"rows": len(rows), "both_parameters": both, "blank_half_life": len(rows) - both}
        cols = ["Fuel", "Technology", "Period label", "Maximum lifetime (years)", "Retirement half-life (years)"]
        answer = f"{both} of {len(rows)} technology-period rows provide both parameters. The remaining {len(rows) - both} leave half-life blank."
    elif case == "world-crop-rates":
        cols = ["GCAM_commodity", "2050", "2095"]
        rows = _rows(env, cols, ["GCAM_commodity"])
        changed = 0
        for row in rows:
            before, after = (_number(x, signed=True) for x in row[1:3])
            direction = "lower" if after < before else "higher" if after > before else "unchanged"
            row.insert(-1, direction)
            changed += after != before
        cols = ["Commodity", "2050 rate", "2095 rate", "2095 relative to 2050"]
        stats = {"commodities": len(rows), "changed": changed}
        answer = f"The fallback rate changes for {changed} of {len(rows)} commodities between 2050 and 2095."
    elif case == "world-capture-residual":
        cols = ["subsector", "technology", "initial-future-year", "2100"]
        rows = _rows(env, cols, cols[:2])
        for r in rows:
            fractions = [_number(v) for v in r[2:4]]
            if max(fractions) > 1:
                raise SourceSchemaChanged("A carbon capture fraction exceeds one.")
            r[-1:-1] = [str(1 - v) for v in fractions]
        cols = ["Fuel", "Technology", "Initial future capture", "2100 capture", "Initial future residual", "2100 residual"]
        lower = sum(Decimal(r[5]) < Decimal(r[4]) for r in rows)
        stats = {"technologies": len(rows), "lower_residual": lower}
        answer = f"The uncaptured share is lower in 2100 than in the initial future year for {lower} of {len(rows)} technology rows."
    elif case == "world-industry-response":
        cols = ["pc.output_GJ", "inc_elas"]
        rows = _rows(env, cols, ["pc.output_GJ"])
        rows.sort(key=lambda r: _number(r[0]))
        if len({_number(r[0]) for r in rows}) != len(rows):
            raise SourceSchemaChanged("Two rows give the same numeric industrial output level.")
        for r in rows:
            elasticity = _number(r[1], signed=True)
            r.insert(-1, "negative" if elasticity < 0 else "positive" if elasticity > 0 else "zero")
        cols = ["Output (GJ/person/year)", "Income elasticity", "Sign"]
        zeros = [r[0] for r in rows if r[2] == "zero"]
        negatives = [r[0] for r in rows if r[2] == "negative"]
        stats = {"levels": len(rows), "zero_levels": len(zeros), "negative_levels": len(negatives)}
        answer = (f"Output levels with zero elasticity: {', '.join(zeros) or 'none'}. "
                  f"Levels with negative elasticity: {', '.join(negatives) or 'none'}. Output is in GJ per person per year.")
    elif case == "latin-america-regions":
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
