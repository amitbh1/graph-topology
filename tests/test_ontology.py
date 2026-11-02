"""The ontology is the schema contract: these tests keep code and database aligned with it."""

import pytest

from medgraph.ontology import extract_cypher_identifiers, get_ontology
from medgraph.schema import vector_index_statements
from medgraph.validate import check_source


@pytest.fixture(scope="module")
def onto():
    return get_ontology()


def test_ontology_counts(onto):
    assert len(onto.classes) == 16
    assert len(onto.relationships) == 20
    assert len(onto.embedded_classes) == 14


def test_ontology_is_structurally_valid(onto):
    assert onto.validate() == []


def test_every_class_declares_a_key(onto):
    assert all(c.key for c in onto.classes.values())


def test_relationship_domains_and_ranges_reference_declared_classes(onto):
    for r in onto.relationships.values():
        assert r.domain in onto.classes, r.name
        assert r.range in onto.classes, r.name


def test_every_label_and_relationship_in_code_is_declared(onto):
    """Scans all Cypher in the package (ETL, queries, API, assistant, validators)."""
    assert check_source(onto) == []


def test_undeclared_relationship_is_caught(onto):
    # Typo for INDICATED_FOR must be flagged before it reaches the database.
    bad = "MATCH (d:Drug)-[:INDICATION_FOR]->(i:IndicationTerm) RETURN d"
    assert onto.check_cypher(bad) == ["undeclared relationship type :INDICATION_FOR"]


def test_undeclared_label_is_caught(onto):
    assert onto.check_cypher("MATCH (x:Medicine) RETURN x") == ["undeclared node label :Medicine"]


def test_extractor_handles_alternation_and_var_length():
    labels, rels = extract_cypher_identifiers(
        "MATCH (a:ATCLevel)-[:ATC_PARENT*0..4]->(b) MATCH (d)-[r:SUSPECT_IN|HAS_REACTION]->()")
    assert labels == {"ATCLevel"}
    assert rels == {"ATC_PARENT", "SUSPECT_IN", "HAS_REACTION"}


def test_constraints_match_declared_keys(onto):
    stmts = onto.constraint_statements()
    assert len(stmts) == len(onto.classes)
    for c in onto.classes.values():
        assert any(f"(n:{c.name}) REQUIRE n.{c.key} IS UNIQUE" in s for s in stmts)


def test_vector_index_per_embedded_class(onto):
    stmts = vector_index_statements(onto, dim=768)
    assert len(stmts) == 14
    assert all("`vector.dimensions`: 768" in s for s in stmts)


def test_schema_summary_lists_directions(onto):
    summary = onto.schema_summary()
    assert "(:Drug)-[:INDICATED_FOR]->(:IndicationTerm)" in summary
    assert "(:DrugAlias)-[:ALIAS_OF]->(:Drug)" in summary
