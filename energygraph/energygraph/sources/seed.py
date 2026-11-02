"""Synthetic sample world for offline demos and tests — ILLUSTRATIVE DATA ONLY.

Every company, asset, incident, project, price and document here is fictional
(ids prefixed `seed:`). Concept codes reuse real Open Energy Ontology ids so the
seed lines up with the taxonomy once OEO is loaded. Countries are real ISO codes.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Iterator

from .base import SourceConnector, SourceMeta
from .records import (AssetRecord, CompanyRecord, ConceptRecord, ConnectionRecord, DocumentRecord, IncidentRecord,
                      OwnerRef, PriceRecord, PriceSeriesRecord, ProjectRecord)

AS_OF = dt.date(2026, 1, 1)
S = "seed"

CONCEPTS = [
    ("oeo:OEO_00020039", "energy carrier", [], []),
    ("oeo:OEO_00000173", "fuel", ["oeo:OEO_00020039"], []),
    ("oeo:OEO_00000292", "natural gas", ["oeo:OEO_00000173"], ["gas", "methane gas"]),
    ("oeo:OEO_00010237", "liquefied natural gas", ["oeo:OEO_00000292"], ["lng"]),
    ("oeo:OEO_00000115", "crude oil", ["oeo:OEO_00000173"], ["crude", "oil"]),
    ("oeo:OEO_00000088", "coal", ["oeo:OEO_00000173"], []),
    ("oeo:OEO_00020085", "renewable energy", [], ["renewables"]),
    ("oeo:OEO_00000446", "wind energy", ["oeo:OEO_00020085"], ["wind"]),
    ("oeo:OEO_00000384", "solar energy", ["oeo:OEO_00020085"], ["solar"]),
]

COMPANIES = [
    ("seed:northwind-holdings", "Northwind Holdings plc", "GBR", None, ["Northwind Group"]),
    ("seed:northwind-energy", "Northwind Energy Inc.", "USA", "seed:northwind-holdings", ["Northwind"]),
    ("seed:harbor-lng-partners", "Harbor LNG Partners LP", "USA", "seed:northwind-energy", ["Harbor LNG"]),
    ("seed:meridian-power", "Meridian Power Company", "USA", None, ["Meridian"]),
    ("seed:atlas-refining", "Atlas Refining Ltd", "GBR", None, []),
]

# id, type, name, country, lat, lon, capacity, unit, status, year, concepts, owners[(id, share)], operator, aliases
ASSETS = [
    ("seed:harbor-lng", "LNGTerminal", "Harbor Point LNG", "USA", 29.7, -93.9, 15.0, "mtpa", "operating", 2016,
     ["liquefied natural gas", "lng liquefaction"], [("seed:northwind-energy", 0.6), ("seed:meridian-power", 0.4)],
     "seed:harbor-lng-partners", ["Harbor Point LNG Terminal", "HPLNG"]),
    ("seed:coastal-pipeline", "Pipeline", "Coastal Header Pipeline", "USA", 30.1, -93.5, 4500.0, "MMcf/d", "operating",
     2015, ["natural gas"], [("seed:northwind-energy", 1.0)], "seed:northwind-energy", []),
    ("seed:delta-field", "Field", "Delta Basin Gas Field", "USA", 31.0, -94.2, 2000.0, "MMcf/d", "operating", 2009,
     ["natural gas"], [("seed:meridian-power", 1.0)], "seed:meridian-power", []),
    ("seed:atlas-refinery", "Refinery", "Atlas Thames Refinery", "GBR", 51.5, 0.5, 220.0, "kb/d", "operating", 1972,
     ["crude oil", "oil refining"], [("seed:atlas-refining", 1.0)], "seed:atlas-refining", []),
    ("seed:meridian-ccgt-1", "PowerPlant", "Meridian Bayou CCGT 1", "USA", 30.2, -91.1, 800.0, "MW", "operating", 2012,
     ["natural gas"], [("seed:meridian-power", 1.0)], "seed:meridian-power", []),
    ("seed:meridian-ccgt-2", "PowerPlant", "Meridian Bayou CCGT 2", "USA", 30.2, -91.0, 760.0, "MW", "operating", 2014,
     ["natural gas"], [("seed:meridian-power", 1.0)], "seed:meridian-power", []),
    ("seed:harbor-ccgt", "PowerPlant", "Harbor Point Energy Center", "USA", 29.8, -93.8, 840.0, "MW", "operating", 2011,
     ["natural gas"], [("seed:northwind-energy", 1.0)], "seed:northwind-energy", []),
    ("seed:northwind-wind", "PowerPlant", "Northwind Prairie Wind Farm", "USA", 35.2, -101.8, 300.0, "MW", "operating",
     2019, ["wind energy"], [("seed:northwind-energy", 1.0)], "seed:northwind-energy", []),
    ("seed:atlas-coal", "PowerPlant", "Atlas Fen Power Station", "GBR", 52.6, 0.3, 900.0, "MW", "retired", 1968,
     ["coal"], [("seed:atlas-refining", 1.0)], None, []),
]

CONNECTIONS = [("seed:delta-field", "seed:coastal-pipeline", 2000.0, "MMcf/d"),
               ("seed:coastal-pipeline", "seed:harbor-lng", 4500.0, "MMcf/d"),
               ("seed:coastal-pipeline", "seed:harbor-ccgt", 150.0, "MMcf/d")]

INCIDENTS = [
    ("seed:inc-0001", "seed:harbor-lng", "unplanned_outage", dt.date(2025, 3, 4), dt.date(2025, 3, 9), 5.0, "mtpa",
     "Train 2 tripped following a compressor fault; restarted after repairs."),
    ("seed:inc-0002", "seed:harbor-lng", "planned_outage", dt.date(2025, 9, 1), dt.date(2025, 9, 21), 5.0, "mtpa",
     "Scheduled maintenance on Train 1."),
    ("seed:inc-0003", "seed:meridian-ccgt-1", "forced_outage", dt.date(2025, 7, 15), dt.date(2025, 7, 18), 400.0, "MW",
     "Steam turbine outage during heat wave."),
    ("seed:inc-0004", "seed:coastal-pipeline", "safety", dt.date(2024, 11, 2), dt.date(2024, 11, 3), None, None,
     "Minor release at a compressor station; contained."),
]

PROJECTS = [
    ("seed:proj-harbor-t4", "Harbor Point LNG Train 4", "LNGTerminal", "fid", 5.0, "mtpa", "USA",
     ["seed:harbor-lng-partners"], "seed:harbor-lng", 2029, ["liquefied natural gas"]),
    ("seed:proj-northwind-offshore", "Northwind Gulf Offshore Wind", "PowerPlant", "permitting", 1200.0, "MW", "USA",
     ["seed:northwind-energy"], None, 2031, ["wind energy"]),
    ("seed:proj-meridian-ccgt-3", "Meridian Bayou CCGT 3", "PowerPlant", "announced", 750.0, "MW", "USA",
     ["seed:meridian-power"], None, 2030, ["natural gas"]),
]

SERIES = [
    ("seed:gulf-gas-daily", "Example Gulf Coast gas price (synthetic)", "USD/MMBtu", "natural gas",
     "hub:example-gulf", "Example Gulf Hub", "USA", 3.0, 0.4),
    ("seed:north-sea-crude-daily", "Example North Sea crude price (synthetic)", "USD/bbl", "crude oil",
     "hub:example-north-sea", "Example North Sea Hub", "GBR", 78.0, 4.0),
]

DOCUMENTS = [
    ("seed:doc-0001", "Harbor Point LNG takes final investment decision on Train 4",
     "Harbor LNG Partners LP said it reached a final investment decision on a fourth liquefaction train at Harbor "
     "Point LNG, adding 5 mtpa of capacity. Feed gas will arrive through the Coastal Header Pipeline.",
     dt.date(2025, 10, 2), ["seed:harbor-lng", "seed:coastal-pipeline"], ["seed:harbor-lng-partners"]),
    ("seed:doc-0002", "Meridian Bayou CCGT 1 returns after forced outage",
     "Meridian Power Company restored Meridian Bayou CCGT 1 after a three-day steam turbine outage during the July "
     "heat wave.", dt.date(2025, 7, 19), ["seed:meridian-ccgt-1"], ["seed:meridian-power"]),
]


def synthetic_prices(base: float, amp: float, days: int = 120) -> list[tuple[dt.date, float]]:
    start = dt.date(2025, 9, 1)
    return [(start + dt.timedelta(days=i), round(base + amp * math.sin(i / 9) + amp * 0.3 * math.cos(i / 3), 3))
            for i in range(days)]


class SeedSource(SourceConnector):
    meta = SourceMeta(
        source_id="seed", title="Synthetic sample world (illustrative only)", kind="internal",
        license="Synthetic — no real-world facts", attribution="EnergyGraph sample data", url="",
        refresh="static", record_kinds=("concept", "company", "asset", "connection", "incident", "project",
                                        "price_series", "price", "document"),
    )

    def fetch(self, limit: int | None = None) -> Iterator:
        for code, label, parents, syn in CONCEPTS:
            yield ConceptRecord(source=S, as_of=AS_OF, code=code, label=label, parents=parents, synonyms=syn)
        for cid, name, country, parent, aliases in COMPANIES:
            yield CompanyRecord(source=S, as_of=AS_OF, company_id=cid, name=name, country=country,
                                parent_company_id=parent, aliases=aliases)
        for (aid, atype, name, country, lat, lon, cap, unit, status, year, concepts, owners, operator,
             aliases) in ASSETS:
            yield AssetRecord(source=S, as_of=AS_OF, asset_id=aid, asset_type=atype, name=name, country=country,
                              latitude=lat, longitude=lon, capacity=cap, capacity_unit=unit, status=status,
                              commissioning_year=year, concepts=concepts, aliases=aliases, operator_id=operator,
                              owners=[OwnerRef(company_id=c, share=s) for c, s in owners])
        for a, b, cap, unit in CONNECTIONS:
            yield ConnectionRecord(source=S, as_of=AS_OF, from_asset_id=a, to_asset_id=b, capacity=cap,
                                   capacity_unit=unit)
        for iid, aid, itype, start, end, cap, unit, desc in INCIDENTS:
            yield IncidentRecord(source=S, as_of=AS_OF, incident_id=iid, asset_id=aid, incident_type=itype,
                                 start=start, end=end, capacity_offline=cap, capacity_unit=unit, description=desc)
        for pid, name, atype, status, cap, unit, country, devs, expands, year, concepts in PROJECTS:
            yield ProjectRecord(source=S, as_of=AS_OF, project_id=pid, name=name, asset_type=atype, status=status,
                                capacity=cap, capacity_unit=unit, country=country, developer_ids=devs,
                                expands_asset_id=expands, expected_year=year, concepts=concepts)
        for sid, name, unit, commodity, hub, hub_name, hub_country, base, amp in SERIES:
            yield PriceSeriesRecord(source=S, as_of=AS_OF, series_id=sid, name=name, unit=unit, frequency="daily",
                                    commodity=commodity, hub_code=hub, hub_name=hub_name, hub_country=hub_country)
            for day, value in synthetic_prices(base, amp):
                yield PriceRecord(source=S, as_of=AS_OF, series_id=sid, date=day, value=value)
        for did, title, text, published, assets, companies in DOCUMENTS:
            yield DocumentRecord(source=S, as_of=AS_OF, doc_id=did, title=title, text=text, published_at=published,
                                 asset_ids=assets, company_ids=companies)
