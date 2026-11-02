"""openFDA drug labels -> Drug, DrugAlias, DrugLabel, Manufacturer, PharmClass, IndicationTerm."""

from __future__ import annotations

import re

from tqdm import tqdm

from ..config import settings
from ..db import Neo4jClient
from .common import TermMatcher, affirmative_sentences, get_json, lucene_value, norm
from .rxnorm import canonical_ingredient

LABEL_URL = "https://api.fda.gov/drug/label.json"

# Drugs always included so the worked examples (Glucotrol / Glipizide ...) exist.
PRIORITY_DRUGS = [
    "glipizide", "glimepiride", "glyburide", "metformin", "sitagliptin", "pioglitazone",
    "empagliflozin", "dapagliflozin", "tolvaptan", "lisinopril", "losartan", "amlodipine",
    "atorvastatin", "simvastatin", "levothyroxine", "omeprazole", "sertraline", "warfarin",
]

# Too generic to be useful as an indication on their own.
INDICATION_STOPWORDS = {
    "disease", "diseases", "syndrome", "disorder", "disorders", "infection", "infections",
    "pain", "death", "injury", "injuries", "lesion", "lesions", "tumor", "tumors", "cancer",
    "control", "risk", "acute", "chronic", "attack", "shock", "fever", "edema",
}

MAX_TEXT = 6000


def _first(openfda: dict, key: str) -> str | None:
    vals = openfda.get(key) or []
    return vals[0] if vals else None


def _text(label: dict, key: str) -> str:
    return " ".join(label.get(key) or [])[:MAX_TEXT]


def fetch_labels(limit: int) -> list[dict]:
    labels: dict[str, dict] = {}
    for drug in PRIORITY_DRUGS:
        data = get_json(LABEL_URL, {"search": f"openfda.generic_name:{lucene_value(drug)}", "limit": 3}) or {}
        for lab in data.get("results", []):
            labels[lab["id"]] = lab
    query = "_exists_:indications_and_usage AND _exists_:openfda.generic_name"
    skip = 0
    while len(labels) < limit:
        data = get_json(LABEL_URL, {"search": query, "limit": 100, "skip": skip}) or {}
        results = data.get("results", [])
        if not results:
            break
        for lab in results:
            labels.setdefault(lab["id"], lab)
        skip += 100
    return list(labels.values())[:limit]


def build_indication_matcher(client: Neo4jClient) -> TermMatcher:
    rows = client.read(
        "MATCH (d:MeshDescriptor {is_disease: true})-[:HAS_ENTRY_TERM]->(t:MeshEntryTerm) "
        "RETURN t.name AS term, d.ui AS ui"
    )
    return TermMatcher((r["term"], r["ui"]) for r in rows if r["term"] not in INDICATION_STOPWORDS)


def transform(label: dict, matcher: TermMatcher | None) -> dict | None:
    openfda = label.get("openfda") or {}
    generic = _first(openfda, "generic_name")
    if not generic:
        return None
    drug, rxcui = canonical_ingredient(generic)
    brand = _first(openfda, "brand_name")
    indications = _text(label, "indications_and_usage")
    aliases = {norm(generic): ("generic", generic)}
    for b in openfda.get("brand_name") or []:
        aliases[norm(b)] = ("brand", b)
    aliases.pop(drug, None)
    found = matcher.find(affirmative_sentences(indications)) if matcher else []
    return {
        "drug": drug,
        "drug_display": drug.title(),
        "rxcui": rxcui,
        "label_id": label["id"],
        "set_id": label.get("set_id"),
        "title": f"{brand or generic} ({generic})".strip(),
        "indications": indications,
        "adverse_reactions": _text(label, "adverse_reactions"),
        "effective_time": label.get("effective_time"),
        "route": _first(openfda, "route"),
        "manufacturer": _first(openfda, "manufacturer_name"),
        "pharm_classes": [re.sub(r"\s*\[EPC\]$", "", c) for c in openfda.get("pharm_class_epc") or []],
        "aliases": [{"name": k, "kind": v[0], "display": v[1]} for k, v in aliases.items()],
        "indication_terms": [{"name": t, "ui": ui} for t, ui in found],
    }


LOAD_LABEL = """
UNWIND $rows AS r
MERGE (d:Drug {name: r.drug})
  SET d.display = r.drug_display, d.rxcui = coalesce(d.rxcui, r.rxcui)
MERGE (l:DrugLabel {label_id: r.label_id})
  SET l.set_id = r.set_id, l.title = r.title, l.indications = r.indications,
      l.adverse_reactions = r.adverse_reactions, l.effective_time = r.effective_time,
      l.route = r.route, l.source = coalesce(r.source, 'openFDA')
MERGE (d)-[:HAS_LABEL]->(l)
FOREACH (m IN CASE WHEN r.manufacturer IS NULL THEN [] ELSE [r.manufacturer] END |
  MERGE (mf:Manufacturer {name: toLower(m)}) ON CREATE SET mf.display = m
  MERGE (l)-[:MANUFACTURED_BY]->(mf))
FOREACH (pc IN r.pharm_classes |
  MERGE (p:PharmClass {name: toLower(pc)}) ON CREATE SET p.display = pc
  MERGE (d)-[:HAS_PHARM_CLASS]->(p))
FOREACH (a IN r.aliases |
  MERGE (al:DrugAlias {name: a.name}) ON CREATE SET al.display = a.display, al.kind = a.kind, al.source = 'openFDA'
  MERGE (al)-[:ALIAS_OF]->(d))
WITH d, r
UNWIND r.indication_terms AS it
MERGE (i:IndicationTerm {name: it.name}) ON CREATE SET i.display = it.name
MERGE (d)-[:INDICATED_FOR]->(i)
WITH i, it
MATCH (m:MeshDescriptor {ui: it.ui})
MERGE (i)-[:INDICATION_MAPS_TO]->(m)
"""


def load_labels(client: Neo4jClient, limit: int | None = None) -> None:
    limit = limit or settings.label_limit
    matcher = build_indication_matcher(client)
    raw = fetch_labels(limit)
    rows = [r for r in (transform(l, matcher) for l in tqdm(raw, desc="labels")) if r]
    client.write_batches(LOAD_LABEL, rows, size=100)
    print(f"labels: {len(rows)} documents, {len({r['drug'] for r in rows})} drugs, "
          f"{sum(len(r['indication_terms']) for r in rows)} indication mentions")
