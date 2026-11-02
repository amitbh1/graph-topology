"""US EIA Open Data API v2 -> published spot price series (requires a free API key).

Licence: US public domain. Attribution: U.S. Energy Information Administration.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterator

from ...config import settings
from ...etl.common import get_json
from ..base import SourceConnector, SourceMeta
from ..records import PriceRecord, PriceSeriesRecord

API = "https://api.eia.gov/v2"

SERIES = [
    # (series id, route, facet, name, unit, commodity, hub code, hub name, hub country)
    ("eia:RWTC", "petroleum/pri/spt", "RWTC", "WTI crude oil spot price, Cushing OK", "USD/bbl", "crude oil",
     "hub:cushing", "Cushing, Oklahoma", "USA"),
    ("eia:RBRTE", "petroleum/pri/spt", "RBRTE", "Brent crude oil spot price, Europe", "USD/bbl", "crude oil",
     "hub:brent", "Brent (North Sea)", "GBR"),
    ("eia:RNGWHHD", "natural-gas/pri/fut", "RNGWHHD", "Henry Hub natural gas spot price", "USD/MMBtu",
     "natural gas", "hub:henry-hub", "Henry Hub, Louisiana", "USA"),
]


class EiaPrices(SourceConnector):
    meta = SourceMeta(
        source_id="eia", title="U.S. EIA Open Data — spot prices", kind="public", license="Public domain (US Government)",
        attribution="U.S. Energy Information Administration (EIA)", url="https://www.eia.gov/opendata/",
        refresh="daily", requires=("EIA_API_KEY",), record_kinds=("price_series", "price"),
    )

    def __init__(self, start: str = "2023-01-01"):
        self.start = start

    def available(self) -> tuple[bool, str]:
        return (True, "ok") if settings.eia_api_key else (False, "EIA_API_KEY not set (free key: eia.gov/opendata)")

    def fetch(self, limit: int | None = None) -> Iterator:
        today = dt.date.today()
        for sid, route, facet, name, unit, commodity, hub, hub_name, hub_country in SERIES:
            yield PriceSeriesRecord(source="eia", series_id=sid, name=name, unit=unit, frequency="daily",
                                    commodity=commodity, hub_code=hub, hub_name=hub_name, hub_country=hub_country,
                                    methodology_url="https://www.eia.gov/dnav/pet/TblDefs/pet_pri_spt_tbldef2.asp",
                                    as_of=today)
            params = {"api_key": settings.eia_api_key, "frequency": "daily", "data[0]": "value",
                      "facets[series][]": facet, "start": self.start, "sort[0][column]": "period",
                      "sort[0][direction]": "desc", "length": limit or 5000}
            data = get_json(f"{API}/{route}/data/", params, cache=False) or {}
            for row in (data.get("response") or {}).get("data", []):
                if row.get("value") in (None, ""):
                    continue
                yield PriceRecord(source="eia", series_id=sid, date=dt.date.fromisoformat(row["period"]),
                                  value=float(row["value"]), as_of=today)
