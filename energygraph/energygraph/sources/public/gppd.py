"""WRI Global Power Plant Database v1.3 -> PowerPlant assets + owner companies.

Licence: CC BY 4.0. Attribution: World Resources Institute. The database is no
longer updated (last release 2021): every record carries as_of from the file.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from typing import Iterator

from ...config import settings
from ...etl.common import download, norm
from ..base import SourceConnector, SourceMeta
from ..records import AssetRecord, CompanyRecord, OwnerRef
from .core import FUEL_LABELS

CSV_NAME = "global_power_plant_database.csv"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:100] or "unknown"


def owner_id(name: str) -> str:
    return f"name:{slug(name)}"


def _float(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transform_row(row: dict, source: str = "gppd") -> tuple[AssetRecord, CompanyRecord | None]:
    year = _float(row.get("year_of_capacity_data"))
    as_of = dt.date(int(year), 12, 31) if year else dt.date(2021, 6, 2)
    fuels = [row.get(k) for k in ("primary_fuel", "other_fuel1", "other_fuel2", "other_fuel3")]
    concepts = [FUEL_LABELS.get(norm(f), norm(f)) for f in fuels if f]
    owner = (row.get("owner") or "").strip()
    company = CompanyRecord(source=source, company_id=owner_id(owner), name=owner[:300], as_of=as_of) if owner else None
    commissioned = _float(row.get("commissioning_year"))
    asset = AssetRecord(
        source=source, as_of=as_of, asset_id=f"gppd:{row['gppd_idnr']}", asset_type="PowerPlant",
        name=row["name"][:300], country=row["country"], country_name=row.get("country_long") or None,
        latitude=_float(row.get("latitude")), longitude=_float(row.get("longitude")),
        capacity=_float(row.get("capacity_mw")), capacity_unit="MW", status="operating",
        commissioning_year=int(commissioned) if commissioned and 1850 <= commissioned <= 2100 else None,
        concepts=list(dict.fromkeys(concepts)),
        owners=[OwnerRef(company_id=company.company_id)] if company else [],
    )
    return asset, company


class GlobalPowerPlantDatabase(SourceConnector):
    meta = SourceMeta(
        source_id="gppd", title="WRI Global Power Plant Database v1.3", kind="public", license="CC BY 4.0",
        attribution="Global Energy Observatory, Google, KTH Royal Institute of Technology in Stockholm, "
                    "Enipedia, World Resources Institute. 2018. Global Power Plant Database.",
        url="https://datasets.wri.org/dataset/globalpowerplantdatabase", refresh="none (final release 2021)",
        record_kinds=("asset", "company"),
    )

    def fetch(self, limit: int | None = None) -> Iterator:
        path = download(settings.gppd_url, "gppd.zip")
        seen_companies: set[str] = set()
        with zipfile.ZipFile(path) as zf, zf.open(CSV_NAME) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            n = 0
            for row in reader:
                if (_float(row.get("capacity_mw")) or 0) < settings.gppd_min_mw:
                    continue
                asset, company = transform_row(row, self.meta.source_id)
                if company and company.company_id not in seen_companies:
                    seen_companies.add(company.company_id)
                    yield company
                yield asset
                n += 1
                if limit and n >= limit:
                    break
