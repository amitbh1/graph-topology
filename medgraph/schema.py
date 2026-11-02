"""Create Neo4j constraints and indexes from the ontology."""

from __future__ import annotations

from .config import settings
from .db import Neo4jClient
from .ontology import Ontology, get_ontology

FULLTEXT_INDEXES = [
    # Graph-assisted keyword search over medical documents.
    "CREATE FULLTEXT INDEX druglabel_text IF NOT EXISTS "
    "FOR (n:DrugLabel) ON EACH [n.title, n.indications, n.adverse_reactions]",
    # Fuzzy name lookups for entity resolution.
    "CREATE FULLTEXT INDEX entity_names IF NOT EXISTS "
    "FOR (n:Drug|DrugAlias|MeshEntryTerm|Reaction|Condition) ON EACH [n.display]",
]


def vector_index_name(label: str) -> str:
    return f"{label.lower()}_embedding"


def vector_index_statements(onto: Ontology, dim: int | None = None) -> list[str]:
    dim = dim or settings.embedding_dim
    return [
        f"CREATE VECTOR INDEX {vector_index_name(c.name)} IF NOT EXISTS "
        f"FOR (n:{c.name}) ON n.embedding OPTIONS {{indexConfig: {{"
        f"`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}"
        for c in onto.embedded_classes
    ]


def create_schema(client: Neo4jClient, onto: Ontology | None = None) -> None:
    onto = onto or get_ontology()
    for stmt in onto.constraint_statements() + FULLTEXT_INDEXES + vector_index_statements(onto):
        client.write(stmt)
    client.write("CALL db.awaitIndexes(300)")
    print(f"schema: {len(onto.classes)} constraints, {len(FULLTEXT_INDEXES)} fulltext, "
          f"{len(onto.embedded_classes)} vector indexes")


def database_constraints(client: Neo4jClient) -> dict[str, str]:
    """label -> key property, for UNIQUENESS constraints present in the database."""
    rows = client.read(
        "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
        "WHERE type = 'UNIQUENESS' RETURN labelsOrTypes, properties"
    )
    return {r["labelsOrTypes"][0]: r["properties"][0] for r in rows}
