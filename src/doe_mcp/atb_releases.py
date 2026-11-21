"""Publisher-listed releases, pinned to the bytes captured by record_atb.py."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Release:
    edition: int
    release: str
    url: str
    bytes: int
    sha256: str
    currency_basis: str
    currency_source: str


RELEASES = {
    2024: Release(2024, "v4.0.0",
        "https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/parquet/2024/v4.0.0/ATBe.parquet",
        5131651, "13047eac66774851e861c3e9fa45c52d8b7f819cd3f21b1a41762125f466690e",
        "2022 USD unless noted otherwise by the publisher; nonmonetary metrics keep their own units",
        "https://atb.nlr.gov/electricity/2024/changes_in_2024"),
    2025: Release(2025, "v2.0.0",
        "https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/parquet/2025/v2.0.0/ATBe.parquet",
        9193709, "c380220b472b7832d864afe9dd15d6c748aee0a7f58a6ffc26d89261413502b4",
        "2023 USD unless noted otherwise by the publisher; nonmonetary metrics keep their own units",
        "https://atb.nlr.gov/electricity/2025/definitions"),
}
COMMON_COLUMNS = ("atb_year", "core_metric_key", "core_metric_parameter", "core_metric_case",
    "tax_credit_case", "crpyears", "technology", "technology_alias", "techdetail", "techdetail2",
    "resourcedetail", "display_name", "default", "scale", "maturity", "scenario",
    "core_metric_variable", "units", "value")
FILTER_COLUMNS = set(COMMON_COLUMNS) - {"value", "core_metric_key"}


def release_for(edition: int) -> Release:
    from .core.errors import InvalidQuery
    if type(edition) is not int or edition not in RELEASES:
        raise InvalidQuery("Specify ATB edition 2024 or 2025. There is no latest alias or verified 2026 release here.")
    return RELEASES[edition]
