"""ClinicalTrials.gov API v2 -> ClinicalTrial, Sponsor, Condition, Intervention.

Trials are fetched for the disease concepts that already have drugs in the
graph, so research can be connected to approved uses.
"""

from __future__ import annotations

from tqdm import tqdm

from ..config import settings
from ..db import Neo4jClient
from .common import get_json, norm

STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"


def transform_study(study: dict) -> dict:
    ps = study.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    sponsor = ((ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name")
    conds = (ps.get("conditionsModule") or {}).get("conditions") or []
    interventions = (ps.get("armsInterventionsModule") or {}).get("interventions") or []
    meshes = ((study.get("derivedSection") or {}).get("conditionBrowseModule") or {}).get("meshes") or []
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "phases": design.get("phases") or [],
        "study_type": design.get("studyType"),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "sponsor": sponsor,
        "conditions": [{"name": norm(c), "display": c} for c in conds],
        "mesh_uis": [m["id"] for m in meshes if m.get("id")],
        "drugs": sorted({norm(i["name"]) for i in interventions
                         if i.get("type") in ("DRUG", "BIOLOGICAL") and i.get("name")}),
    }


LOAD_TRIAL = """
UNWIND $rows AS r
MERGE (t:ClinicalTrial {nct_id: r.nct_id})
  SET t.title = r.title, t.status = r.status, t.start_date = r.start_date, t.phases = r.phases,
      t.study_type = r.study_type, t.enrollment = r.enrollment, t.source = coalesce(r.source, 'ClinicalTrials.gov')
FOREACH (s IN CASE WHEN r.sponsor IS NULL THEN [] ELSE [r.sponsor] END |
  MERGE (sp:Sponsor {name: toLower(s)}) ON CREATE SET sp.display = s
  MERGE (t)-[:SPONSORED_BY]->(sp))
FOREACH (c IN r.conditions |
  MERGE (cn:Condition {name: c.name}) ON CREATE SET cn.display = c.display
  MERGE (t)-[:STUDIES_CONDITION]->(cn))
FOREACH (dn IN r.drugs |
  MERGE (iv:Intervention {name: dn})
  MERGE (t)-[:TESTS_INTERVENTION]->(iv))
WITH t, r
UNWIND r.mesh_uis AS ui
MATCH (m:MeshDescriptor {ui: ui})
MATCH (t)-[:STUDIES_CONDITION]->(cn:Condition)
WHERE EXISTS { (m)-[:HAS_ENTRY_TERM]->(:MeshEntryTerm {name: cn.name}) } OR size(r.conditions) = 1
MERGE (cn)-[:CONDITION_MAPS_TO]->(m)
"""

POST_LINKS = [
    # Condition text that is itself a MeSH entry term.
    """MATCH (c:Condition) WHERE NOT (c)-[:CONDITION_MAPS_TO]->()
       MATCH (:MeshEntryTerm {name: c.name})<-[:HAS_ENTRY_TERM]-(m:MeshDescriptor)
       MERGE (c)-[:CONDITION_MAPS_TO]->(m)""",
    # Interventions resolved to canonical drugs, directly or through an alias.
    """MATCH (i:Intervention) WHERE NOT (i)-[:INTERVENTION_IS]->()
       OPTIONAL MATCH (d:Drug {name: i.name})
       OPTIONAL MATCH (:DrugAlias {name: i.name})-[:ALIAS_OF]->(ad:Drug)
       WITH i, coalesce(d, ad) AS drug WHERE drug IS NOT NULL
       MERGE (i)-[:INTERVENTION_IS]->(drug)""",
]


def load_trials(client: Neo4jClient, conditions: int | None = None, per_condition: int | None = None) -> None:
    conditions = conditions or settings.trial_conditions
    per_condition = per_condition or settings.trials_per_condition
    targets = client.read(
        "MATCH (:Drug)-[:INDICATED_FOR]->(:IndicationTerm)-[:INDICATION_MAPS_TO]->(m:MeshDescriptor) "
        "RETURN m.name AS name, count(*) AS n ORDER BY n DESC LIMIT $k", k=conditions)
    rows: dict[str, dict] = {}
    for t in tqdm(targets, desc="trials"):
        data = get_json(STUDIES_URL, {"query.cond": t["name"], "pageSize": per_condition,
                                      "format": "json"}) or {}
        for study in data.get("studies", []):
            row = transform_study(study)
            if row["nct_id"]:
                rows[row["nct_id"]] = row
    client.write_batches(LOAD_TRIAL, list(rows.values()), size=100)
    for q in POST_LINKS:
        client.write(q)
    print(f"trials: {len(rows)} studies for {len(targets)} conditions")
