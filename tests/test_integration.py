"""Read-only checks against a running, loaded Neo4j (seed or full ETL). Skipped otherwise.

    docker compose up -d neo4j && python -m medgraph seed && pytest
"""

import pytest

from medgraph import queries as q
from medgraph.db import Neo4jClient
from medgraph.ontology import get_ontology
from medgraph.validate import check_database


@pytest.fixture(scope="module")
def db():
    client = Neo4jClient()
    if not client.ping():
        pytest.skip("Neo4j not reachable")
    if not client.read("MATCH (d:Drug {name: 'glipizide'}) RETURN d LIMIT 1"):
        pytest.skip("graph not loaded (run `python -m medgraph seed`)")
    yield client
    client.close()


def test_database_matches_ontology(db):
    assert check_database(get_ontology(), db) == []


def test_brand_name_resolves_to_generic(db):
    r = q.resolve_drug(db, "Glucotrol")
    assert r["drug"] == "glipizide" and r["via"].startswith("alias")


def test_decode_atc_glipizide(db):
    r = q.decode_atc(db, "Glucotrol")
    path = [p["code"] for p in r["classifications"][0]["path"]]
    assert path == ["A", "A10", "A10B", "A10BB", "A10BB07"]


def test_mesh_expansion_includes_narrower_and_synonyms(db):
    r = q.mesh_expand(db, "Diabetes Mellitus")
    assert r["found"] and r["concept"]["ui"] == "D003920"
    assert "diabetes mellitus, type 2" in r["terms"]
    assert r["term_count"] > 5


def test_graph_expansion_improves_recall(db):
    r = q.search_documents(db, "Diabetes Mellitus")
    assert r["expanded_matches"] >= r["baseline_matches"]


def test_drug_safety_glipizide(db):
    r = q.drug_safety(db, "glipizide")
    assert r["found"] and r["reports"] > 0
    assert r["serious"] <= r["reports"]


def test_similar_drugs_glipizide(db):
    r = q.similar_drugs(db, "glipizide")
    assert r["total_similar"] > 0
    top = r["similar"][0]
    assert top["score"] == top["shared_indications"] * 2 + top["shared_reactions"]


def test_trials_for_condition_include_narrower_concepts(db):
    r = q.trials_pipeline(db, condition="diabetes mellitus")
    assert r["total_trials"] > 0


def test_unknown_drug_is_reported_not_guessed(db):
    r = q.drug_safety(db, "zzqxv-not-a-drug")
    assert r["found"] is False


def test_disease_overview(db):
    r = q.disease_overview(db, "diabetes mellitus")
    assert "glipizide" in [d["drug"] for d in r["drugs"]]
