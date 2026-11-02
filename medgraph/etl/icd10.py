"""NLM Clinical Tables -> ICD-10-CM codes for the *active* disease concepts
(MeSH disease descriptors that drugs are indicated for)."""

from __future__ import annotations

from tqdm import tqdm

from ..db import Neo4jClient
from .common import get_json

ICD_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

ACTIVE_DISEASES = """
MATCH (m:MeshDescriptor {is_disease: true})
WHERE NOT (m)-[:HAS_ICD10]->()
  AND EXISTS { (m)<-[:INDICATION_MAPS_TO]-(:IndicationTerm)<-[:INDICATED_FOR]-(:Drug) }
RETURN m.ui AS ui, m.name AS name
"""


def search_icd10(term: str, max_list: int = 3) -> list[tuple[str, str]]:
    data = get_json(ICD_URL, {"sf": "code,name", "terms": term, "maxList": max_list})
    if not data or len(data) < 4 or not data[3]:
        return []
    return [(code, name) for code, name in data[3]]


def load_icd10(client: Neo4jClient) -> None:
    rows = []
    for d in tqdm(client.read(ACTIVE_DISEASES), desc="icd10"):
        hits = search_icd10(d["name"])
        if hits:
            code, name = hits[0]
            rows.append({"ui": d["ui"], "code": code, "name": name})
    client.write_batches(
        "UNWIND $rows AS r MATCH (m:MeshDescriptor {ui: r.ui}) "
        "MERGE (c:ICD10Code {code: r.code}) SET c.name = r.name "
        "MERGE (m)-[:HAS_ICD10]->(c)",
        rows,
    )
    print(f"icd10: {len(rows)} disease concepts mapped")
