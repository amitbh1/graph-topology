"""Drug similarity computed in plain Cypher (no Graph Data Science plugin).

    score = shared_indications * 2 + shared_reactions

An exploratory measure — not a clinical recommendation and not evidence that
drugs are interchangeable.
"""

from __future__ import annotations

from ..db import Neo4jClient

INDICATION_WEIGHT = 2
REACTION_WEIGHT = 1


def similarity_score(shared_indications: int, shared_reactions: int) -> int:
    return shared_indications * INDICATION_WEIGHT + shared_reactions * REACTION_WEIGHT


COMPUTE = """
MATCH (d1:Drug)
CALL (d1) {
  MATCH (d1)-[:INDICATED_FOR]->(i:IndicationTerm)<-[:INDICATED_FOR]-(d2:Drug)
  WHERE elementId(d1) < elementId(d2)
  RETURN d2, count(DISTINCT i) AS si, 0 AS sr
  UNION ALL
  MATCH (d1)-[:SUSPECT_IN]->(:AdverseEventReport)-[:HAS_REACTION]->(r:Reaction)
        <-[:HAS_REACTION]-(:AdverseEventReport)<-[:SUSPECT_IN]-(d2:Drug)
  WHERE elementId(d1) < elementId(d2)
  RETURN d2, 0 AS si, count(DISTINCT r) AS sr
}
WITH d1, d2, sum(si) AS shared_indications, sum(sr) AS shared_reactions
MERGE (d1)-[s:SIMILAR_TO]->(d2)
SET s.shared_indications = shared_indications,
    s.shared_reactions = shared_reactions,
    s.score = shared_indications * $wi + shared_reactions * $wr
RETURN count(s) AS n
"""


def compute_similarity(client: Neo4jClient) -> None:
    client.write("MATCH ()-[s:SIMILAR_TO]->() DELETE s")
    n = client.write(COMPUTE, wi=INDICATION_WEIGHT, wr=REACTION_WEIGHT)[0]["n"]
    print(f"similarity: {n} SIMILAR_TO relationships")
