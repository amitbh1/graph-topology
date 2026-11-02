"""Wikidata -> oil refineries and LNG terminals with operators/owners.

Licence: CC0 1.0. Query service usage policy requires a descriptive User-Agent.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterator

from ...etl.common import get_json
from ..base import SourceConnector, SourceMeta
from ..records import AssetRecord, CompanyRecord, OwnerRef

SPARQL = "https://query.wikidata.org/sparql"
CLASSES = {"Refinery": "Q12353044", "LNGTerminal": "Q15709854"}
CONCEPTS = {"Refinery": ["crude oil", "oil refining"], "LNGTerminal": ["liquefied natural gas"]}

QUERY = """
SELECT ?item ?itemLabel ?iso3 ?countryLabel ?coord ?inception
       ?operator ?operatorLabel ?operatorLei ?owner ?ownerLabel ?ownerLei WHERE {
  ?item wdt:P31 wd:%s .
  OPTIONAL { ?item wdt:P17 ?country . ?country wdt:P298 ?iso3 . }
  OPTIONAL { ?item wdt:P625 ?coord . }
  OPTIONAL { ?item wdt:P571 ?inception . }
  OPTIONAL { ?item wdt:P137 ?operator . OPTIONAL { ?operator wdt:P1278 ?operatorLei . } }
  OPTIONAL { ?item wdt:P127 ?owner . OPTIONAL { ?owner wdt:P1278 ?ownerLei . } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


def _qid(uri: str | None) -> str | None:
    return uri.rsplit("/", 1)[-1] if uri else None


def _coord(wkt: str | None) -> tuple[float | None, float | None]:
    if not wkt or not wkt.startswith("Point("):
        return None, None
    lon, lat = wkt[6:-1].split()
    return float(lat), float(lon)


def transform_bindings(asset_type: str, bindings: list[dict], source: str = "wikidata") -> Iterator:
    def v(b, k):
        return b.get(k, {}).get("value")

    today = dt.date.today()
    items: dict[str, dict] = {}
    companies: dict[str, CompanyRecord] = {}
    for b in bindings:
        qid = _qid(v(b, "item"))
        label = v(b, "itemLabel")
        if not qid or not label or label == qid:  # unlabeled items are not useful
            continue
        it = items.setdefault(qid, {"label": label, "iso3": v(b, "iso3"), "country": v(b, "countryLabel"),
                                    "coord": v(b, "coord"), "inception": v(b, "inception"),
                                    "owners": {}, "operator": None})
        for role in ("operator", "owner"):
            cq, cname, lei = _qid(v(b, role)), v(b, f"{role}Label"), v(b, f"{role}Lei")
            if not cq or not cname or cname == cq:
                continue
            cid = f"wd:{cq}"
            companies.setdefault(cid, CompanyRecord(source=source, company_id=cid, name=cname[:300],
                                                    lei=lei if lei and len(lei) == 20 else None, as_of=today))
            if role == "operator":
                it["operator"] = cid
            else:
                it["owners"][cid] = True
    yield from companies.values()
    for qid, it in items.items():
        lat, lon = _coord(it["coord"])
        year = int(it["inception"][:4]) if it["inception"] and it["inception"][:4].isdigit() else None
        iso3 = it["iso3"] if it["iso3"] and len(it["iso3"]) == 3 else None
        yield AssetRecord(
            source=source, as_of=today, asset_id=f"wd:{qid}", asset_type=asset_type, name=it["label"][:300],
            country=iso3, country_name=it["country"] if iso3 else None, latitude=lat, longitude=lon,
            commissioning_year=year if year and 1850 <= year <= 2100 else None,
            concepts=CONCEPTS[asset_type], owners=[OwnerRef(company_id=c) for c in it["owners"]],
            operator_id=it["operator"],
        )


class WikidataAssets(SourceConnector):
    meta = SourceMeta(
        source_id="wikidata", title="Wikidata — oil refineries and LNG terminals", kind="public",
        license="CC0 1.0", attribution="Wikidata contributors", url="https://www.wikidata.org/",
        refresh="weekly", record_kinds=("asset", "company"),
    )

    def fetch(self, limit: int | None = None) -> Iterator:
        n = 0
        for asset_type, qclass in CLASSES.items():
            data = get_json(SPARQL, {"query": QUERY % qclass, "format": "json"}) or {}
            for rec in transform_bindings(asset_type, data.get("results", {}).get("bindings", []), self.meta.source_id):
                yield rec
                n += 1
                if limit and n >= limit:
                    return
