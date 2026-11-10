"""Small questions with one exact answer row per step in the released model."""
from __future__ import annotations

from dataclasses import dataclass

from .core.errors import SourceUnavailable


@dataclass(frozen=True)
class Example:
    title: str
    question: str
    skill: str
    tool: str
    filters: dict[str, str]
    field: str
    answer: str
    limit: str
    # For a country lookup the second filter comes from the first response,
    # never from a country-to-region dictionary in the client.
    iso: str = ""
    region_key: str = ""
    companion: tuple[str, dict[str, str], str] | None = None


EXAMPLES = {
    "kenya-soil": Example(
        "Soil carbon recovery in Kenya", "Which recovery time does GCAM assign to Kenya's model region?",
        "trace-soil-carbon", "land.soil_timescales", {}, "soilTimeScale",
        "GCAM assigns a soil carbon recovery time of {value} years to the region containing Kenya.",
        "The regional parameter describes recovery after land-use change. It is not a measurement from a Kenyan field.",
        "ken", "GCAM_region_ID"),
    "japan-geothermal": Example(
        "Japan's geothermal supply curve", "How much heat input does the model assign to Japan's second hydrothermal grade?",
        "read-geothermal-grades", "power.geothermal_curves", {"grade": "grade 2"}, "available",
        "The second grade in GCAM's Japan curve supplies {value} thermal EJ per year of heat input.",
        "The quantity is heat input. Electrical output would also require a conversion efficiency.",
        "jpn", "region_GCAM3"),
    "germany-cooling": Example(
        "Power plant cooling in Western Europe", "Which fresh-water cooling share applies to coal in Germany's legacy model region?",
        "trace-plant-cooling", "water.cooling_shares",
        {"plant_type": "coal", "cooling_system": "recirculating", "water_type": "fresh"}, "2020",
        "The model's 2020 share for recirculating freshwater cooling in Western European coal plants is {value}.",
        "The table covers Western Europe as a group. It cannot establish the share at German plants.",
        "deu", "region_GCAM3"),
    "brazil-bioenergy": Example(
        "Brazil's bioenergy preference", "Which supply preference does the model apply in Brazil?",
        "trace-bioenergy-support", "land.bioenergy_preferences", {}, "preference",
        "GCAM assigns Brazil the bioenergy supply preference “{value}”.",
        "The table adjusts model share weights. Its category does not identify a subsidy or its size.",
        "bra", "GCAM_region_ID"),
    "pakistan-labor": Example(
        "Labor mobility in Pakistan", "How does GCAM parameterize movement of labor between sectors in Pakistan?",
        "trace-labor-mobility", "economy.labor_sectors", {}, "logit.exponent",
        "Pakistan's labor supply row sets the sector mobility exponent to {value}.",
        "An exponent controls the model's wage response. It does not count workers who changed jobs.",
        "pak", "GCAM_region_ID"),
    "desalination-inputs": Example(
        "The energy cost of reverse osmosis", "Which electricity input and non-energy cost does GCAM use for reverse osmosis?",
        "separate-desalination-inputs", "water.desalination_inputs",
        {"technology": "reverse osmosis", "minicam.energy.input": "elect_td_ind"}, "2100",
        "For 2100 the reverse osmosis inputs are {value} GJ of electricity and {other} in 1975 US dollars per cubic metre.",
        "These inputs have different units. Converting them to a total cost requires an electricity price.",
        companion=("water.desalination_costs", {"technology": "reverse osmosis"}, "2100")),
    "fluorinated-gases": Example(
        "Compare two fluorinated gases", "Which 100-year warming potentials did this release use for HFC134a and CF4?",
        "compare-model-warming-potentials", "emissions.warming_potentials", {"Gas": "HFC134a"}, "GWP",
        "GCAM uses 100-year warming potentials of {value} for HFC134a and {other} for CF4.",
        "The source identifies the 2007 IPCC assessment. These factors alone do not quantify emissions.",
        companion=("emissions.warming_potentials", {"Gas": "CF4"}, "GWP")),
    "offshore-wind": Example(
        "Two offshore wind assumptions", "What does GCAM assume for wind class c7 and, separately, for deep-water capital cost?",
        "separate-offshore-wind-classes", "power.wind_capacity_factors", {"wind_class": "c7"}, "CF",
        "Wind class c7 has a capacity factor of {value}. The deep-water capital cost input is {other} in 1975 dollars per GJ.",
        "Wind class and depth class are separate dimensions. Neither table locates a project or predicts its output.",
        companion=("power.offshore_depth_costs", {"depth_class": "deep"}, "capital.overnight")),
}


async def run_example(example: Example, call) -> str:
    async def one(tool, filters):
        env = await call(tool, filters=filters, rows=2)
        if env.data["total_matches"] != 1 or len(env.data["records"]) != 1:
            raise SourceUnavailable(f"{tool}: example needs one matching model input row.")
        return env.data["records"][0]["values"]

    filters = dict(example.filters)
    if example.iso:
        location = await one("economy.country_regions", {"iso": example.iso})
        key = location[example.region_key]
        if not key:
            raise SourceUnavailable("Country mapping has no region for this input table.")
        filters[example.region_key] = key
    value = (await one(example.tool, filters))[example.field]
    if value is None:
        raise SourceUnavailable("The model input cell is blank.")
    other = None
    if example.companion:
        tool, match, field = example.companion
        other = (await one(tool, match))[field]
        if other is None:
            raise SourceUnavailable("The companion input cell is blank.")
    return example.answer.format(value=value, other=other)
