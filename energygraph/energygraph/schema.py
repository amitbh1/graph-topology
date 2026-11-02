"""Create Neo4j constraints and indexes from the ontology."""

from __future__ import annotations

from .config import settings
from .db import Neo4jClient
from .ontology import Ontology, get_ontology

FULLTEXT_INDEXES = [
    "CREATE FULLTEXT INDEX document_text IF NOT EXISTS FOR (n:Document) ON EACH [n.title, n.text]",
    "CREATE FULLTEXT INDEX entity_names IF NOT EXISTS "
    "FOR (n:Asset|AssetAlias|Company|CompanyAlias|Concept|Region|Hub) ON EACH [n.name, n.display, n.label]",
]

PROPERTY_INDEXES = [
    "CREATE INDEX asset_type_country IF NOT EXISTS FOR (n:Asset) ON (n.asset_type, n.country)",
    "CREATE INDEX asset_entitlement IF NOT EXISTS FOR (n:Asset) ON (n.entitlement)",
    "CREATE INDEX concept_label IF NOT EXISTS FOR (n:Concept) ON (n.label_lower)",
    "CREATE INDEX company_name IF NOT EXISTS FOR (n:Company) ON (n.name_lower)",
    "CREATE INDEX project_status IF NOT EXISTS FOR (n:Project) ON (n.status)",
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
    stmts = onto.constraint_statements() + PROPERTY_INDEXES + FULLTEXT_INDEXES + vector_index_statements(onto)
    for stmt in stmts:
        client.write(stmt)
    client.write("CALL db.awaitIndexes(300)")
    print(f"schema: {len(onto.root_classes)} constraints, {len(FULLTEXT_INDEXES)} fulltext, "
          f"{len(onto.embedded_classes)} vector indexes")


def database_constraints(client: Neo4jClient) -> dict[str, str]:
    rows = client.read("SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
                       "WHERE type = 'UNIQUENESS' RETURN labelsOrTypes, properties")
    return {r["labelsOrTypes"][0]: r["properties"][0] for r in rows}
