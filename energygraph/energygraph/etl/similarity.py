"""Asset peer similarity in plain Cypher (no Graph Data Science plugin).

Candidates: same asset type, same country, same capacity unit, capacity within
0.5x–2x, at least one shared fuel/technology, and the SAME entitlement (a score
must never mix licensed and public facts). Each asset keeps its top-K peers.

    score = 2*shared_concepts + 2*capacity_closeness + 1*(|vintage gap| <= 5y) + 1*shared_owner

Exploratory peer grouping — not an investment or operational recommendation.
"""

from __future__ import annotations

from ..db import Neo4jClient

TOP_K = 10
BATCH = 500

COMPUTE = """
UNWIND $ids AS aid
MATCH (a:Asset {asset_id: aid})
CALL (a) {
  MATCH (b:Asset {asset_type: a.asset_type, country: a.country, entitlement: a.entitlement})
  WHERE b <> a AND NOT coalesce(b.stub, false) AND b.capacity_unit = a.capacity_unit
    AND b.capacity >= a.capacity * 0.5 AND b.capacity <= a.capacity * 2
  WITH a, b, COUNT { (a)-[:USES]->(:Concept)<-[:USES]-(b) } AS shared
  WHERE shared > 0
  WITH a, b, shared,
       CASE WHEN a.capacity < b.capacity THEN a.capacity / b.capacity ELSE b.capacity / a.capacity END AS closeness,
       CASE WHEN a.commissioning_year IS NOT NULL AND b.commissioning_year IS NOT NULL
             AND abs(a.commissioning_year - b.commissioning_year) <= 5 THEN 1 ELSE 0 END AS vintage,
       CASE WHEN EXISTS { (a)<-[:OWNS]-(:Company)-[:OWNS]->(b) } THEN 1 ELSE 0 END AS owner
  WITH b, shared, closeness, vintage, owner, 2 * shared + 2 * closeness + vintage + owner AS score
  ORDER BY score DESC LIMIT $k
  RETURN b, shared, closeness, vintage, owner, score
}
MERGE (a)-[s:SIMILAR_TO {entitlement: a.entitlement}]->(b)
SET s.score = round(score, 3), s.shared_concepts = shared, s.capacity_closeness = round(closeness, 3),
    s.vintage_match = vintage = 1, s.shared_owner = owner = 1, s.source = 'energygraph-core'
RETURN count(s) AS n
"""


def similarity_score(shared: int, closeness: float, vintage: bool, owner: bool) -> float:
    return 2 * shared + 2 * closeness + int(vintage) + int(owner)


def compute_similarity(client: Neo4jClient) -> None:
    client.write("MATCH ()-[s:SIMILAR_TO]->() DELETE s")
    ids = [r["id"] for r in client.read(
        "MATCH (a:Asset) WHERE a.capacity IS NOT NULL AND NOT coalesce(a.stub, false) RETURN a.asset_id AS id")]
    total = 0
    for i in range(0, len(ids), BATCH):
        total += client.write(COMPUTE, ids=ids[i:i + BATCH], k=TOP_K)[0]["n"]
    print(f"similarity: {total} SIMILAR_TO relationships for {len(ids)} assets")
