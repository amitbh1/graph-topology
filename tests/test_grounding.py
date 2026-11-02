import pytest

from medgraph.agent.grounding import check_identifiers, guard_cypher
from medgraph.security.sanitize import clean_entity as _clean
from medgraph.embeddings import HashingEmbedder


def test_invented_trial_ids_are_removed():
    evidence = '{"trials": [{"nct_id": "NCT01234567"}]}'
    answer = "Relevant trials: NCT01234567 and NCT09999999."
    text, unverified = check_identifiers(answer, evidence)
    assert unverified == ["NCT09999999"]
    assert "NCT01234567" in text and "NCT09999999 " not in text.split("_Grounding")[0]


def test_grounded_answer_is_untouched():
    text, unverified = check_identifiers("Glipizide is A10BB07.", '{"code": "A10BB07"}')
    assert unverified == [] and text == "Glipizide is A10BB07."


@pytest.mark.parametrize("cypher", [
    "MATCH (d:Drug) DETACH DELETE d",
    "CREATE (:Drug {name: 'x'})",
    "MATCH (d:Drug) SET d.name = 'x'",
    "CALL dbms.listConfig()",
    "LOAD CSV FROM 'file:///x' AS row RETURN row",
    "MATCH (d:Drug) RETURN d; MATCH (n) DELETE n",
])
def test_write_queries_are_rejected(cypher):
    with pytest.raises(ValueError):
        guard_cypher(cypher)


def test_undeclared_schema_is_rejected():
    with pytest.raises(ValueError, match="INDICATION_FOR"):
        guard_cypher("MATCH (d:Drug)-[:INDICATION_FOR]->(i) RETURN i.name")


def test_valid_query_gets_limit():
    q = guard_cypher("```cypher\nMATCH (d:Drug)-[:INDICATED_FOR]->(i:IndicationTerm) RETURN d.name, i.name\n```")
    assert q.endswith("LIMIT 50")
    assert q.startswith("MATCH")


def test_vector_query_call_is_allowed():
    guard_cypher("CALL db.index.vector.queryNodes('drug_embedding', 5, [0.1]) YIELD node RETURN node.name")


def test_clean_tool_arguments():
    assert _clean("drug: Glucotrol") == "Glucotrol"
    assert _clean("'glipizide'") == "glipizide"
    assert _clean("A10BB07") == "A10BB07"


def test_hashing_embedder_is_normalised_and_lexical():
    e = HashingEmbedder(dim=256)
    a, b, c = e.embed_documents(["diabetes mellitus", "diabetes mellitus type 2", "myocardial infarction"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert abs(dot(a, a) - 1) < 1e-9
    assert dot(a, b) > dot(a, c)
