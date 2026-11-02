"""openFDA FAERS -> (:Drug)-[:SUSPECT_IN]->(:AdverseEventReport)-[:HAS_REACTION]->(:Reaction).

FAERS reports are *reported associations*: a report does not prove that the
drug caused the reaction.
"""

from __future__ import annotations

from tqdm import tqdm

from ..config import settings
from ..db import Neo4jClient
from .common import americanize, get_json, lucene_value, norm

EVENT_URL = "https://api.fda.gov/drug/event.json"
OVERSAMPLE = 10  # reports fetched per report kept


def transform_report(rep: dict, query_drug: str) -> dict:
    patient = rep.get("patient") or {}
    suspects: set[str] = set()
    for drug in patient.get("drug") or []:
        if drug.get("drugcharacterization") != "1":
            continue
        names = (drug.get("openfda") or {}).get("generic_name") or [drug.get("medicinalproduct") or ""]
        suspects.update(norm(n) for n in names if n)
    if any(query_drug in s for s in suspects):
        suspects.add(query_drug)
    return {
        "report_id": rep["safetyreportid"],
        "serious": rep.get("serious") == "1",
        "death": rep.get("seriousnessdeath") == "1",
        "hospitalization": rep.get("seriousnesshospitalization") == "1",
        "receive_date": rep.get("receivedate"),
        "country": rep.get("occurcountry") or rep.get("primarysourcecountry"),
        "suspects": sorted(suspects),
        "reactions": sorted({norm(r.get("reactionmeddrapt")) for r in patient.get("reaction") or []
                             if r.get("reactionmeddrapt")}),
    }


LOAD_REPORT = """
UNWIND $rows AS r
MERGE (e:AdverseEventReport {report_id: r.report_id})
  SET e.serious = r.serious, e.death = r.death, e.hospitalization = r.hospitalization,
      e.receive_date = r.receive_date, e.country = r.country, e.source = coalesce(r.source, 'openFDA FAERS')
FOREACH (rx IN r.reactions |
  MERGE (x:Reaction {name: rx}) ON CREATE SET x.display = rx
  MERGE (e)-[:HAS_REACTION]->(x))
WITH e, r
UNWIND r.suspects AS s
OPTIONAL MATCH (d:Drug {name: s})
OPTIONAL MATCH (:DrugAlias {name: s})-[:ALIAS_OF]->(ad:Drug)
WITH e, coalesce(d, ad) AS drug WHERE drug IS NOT NULL
MERGE (drug)-[:SUSPECT_IN]->(e)
"""

MAP_REACTIONS = """
UNWIND $rows AS row
MATCH (r:Reaction {name: row.name})
MATCH (t:MeshEntryTerm)<-[:HAS_ENTRY_TERM]-(m:MeshDescriptor) WHERE t.name IN [row.name, row.us]
MERGE (r)-[:REACTION_MAPS_TO]->(m)
RETURN count(DISTINCT r) AS n
"""


def map_reactions(client: Neo4jClient) -> int:
    """Link reaction terms to MeSH descriptors by (Americanised) entry-term match."""
    names = [r["name"] for r in client.read(
        "MATCH (r:Reaction) WHERE NOT (r)-[:REACTION_MAPS_TO]->() RETURN r.name AS name")]
    rows = [{"name": n, "us": americanize(n)} for n in names]
    return client.write(MAP_REACTIONS, rows=rows)[0]["n"] if rows else 0


def load_faers(client: Neo4jClient, per_drug: int | None = None) -> None:
    per_drug = per_drug or settings.faers_per_drug
    drugs = [r["name"] for r in client.read(
        "MATCH (d:Drug)-[:HAS_LABEL]->() RETURN DISTINCT d.name AS name ORDER BY name")]
    rows: dict[str, dict] = {}
    for drug in tqdm(drugs, desc="faers"):
        # openFDA cannot require that *this* drug entry is the suspect one, and only ~15% of hits
        # list it as suspect — oversample, then keep reports where it really is a suspect drug.
        query = f"patient.drug.openfda.generic_name:{lucene_value(drug)} AND patient.drug.drugcharacterization:1"
        data = get_json(EVENT_URL, {"search": query, "limit": min(per_drug * OVERSAMPLE, 1000)}) or {}
        kept = 0
        for rep in data.get("results", []):
            row = transform_report(rep, drug)
            if drug not in row["suspects"]:
                continue
            if kept >= per_drug:
                break
            kept += 1
            if row["report_id"] in rows:
                row["suspects"] = sorted(set(row["suspects"]) | set(rows[row["report_id"]]["suspects"]))
            rows[row["report_id"]] = row
    client.write_batches(LOAD_REPORT, list(rows.values()), size=200)
    mapped = map_reactions(client)
    print(f"faers: {len(rows)} reports, "
          f"{len({x for r in rows.values() for x in r['reactions']})} reaction terms, {mapped} newly mapped to MeSH")
