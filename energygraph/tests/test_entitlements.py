"""Adversarial entitlement tests: organization-source data must never reach an unentitled caller,
through any query, any tool or the API. Needs the seeded graph."""

import json

import pytest

from energygraph import queries as q
from energygraph.agent.tools import RunState, build_tools, run_state
from energygraph.security.auth import Principal
from energygraph.validate import check_database
from energygraph.ontology import get_ontology

from .conftest import LICENSED, ORG_ONLY, PUBLIC

# Licensed values that never appear in any probe input.
ORG_ONLY_VALUES = ['780.0', 'Kestrel Generation LLC', 'Kestrel Bay Peaker', '11.5', 'OUT-500']

PROBES = {
    "asset_overview": [{"asset": "Kestrel Bay Power Station"}, {"asset": "org:PP-0001"}, {"asset": "Harbor Point LNG"}],
    "ownership_chain": [{"entity": "Meridian Power Company"}, {"entity": "Kestrel Generation LLC"}],
    "taxonomy_expand": [{"term": "natural gas"}],
    "decode_classification": [{"term": "liquefied natural gas"}],
    "search_documents": [{"term": "Kestrel"}, {"term": "natural gas"}],
    "incident_history": [{"entity": "Harbor Point LNG"}, {"entity": "Kestrel Bay Power Station"}],
    "project_pipeline": [{"country": "USA"}, {"concept": "energy storage"}],
    "similar_assets": [{"asset": "Meridian Bayou CCGT 1"}, {"asset": "Harbor Point Energy Center"}],
    "supply_path": [{"origin": "Coastal Header Pipeline"}],
    "price_series": [{"series": "org:BENCH-GULF-LNG"}, {"series": "Example Gulf Hub"}, {"series": "liquefied natural gas"}],
    "semantic_search": [{"text": "gas power station on the Gulf coast"}],
    "graph_qa": [{"question": "List all assets"}],
}


def _principal(ents):
    return Principal("test", frozenset({"read", "chat"}), frozenset(ents), kind="test")


def _invoke(tool, args, ents):
    token = run_state.set(RunState(budget=99, principal=_principal(ents)))
    try:
        return tool.invoke(args)
    finally:
        run_state.reset(token)


def test_database_matches_contract(db):
    assert check_database(get_ontology(), db) == []


@pytest.mark.parametrize("tool_name", sorted(PROBES))
def test_no_org_data_leaks_to_public_principal(db, tool_name, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "hash")
    from energygraph import embeddings
    monkeypatch.setattr(embeddings, "get_embedder", lambda: embeddings.HashingEmbedder())
    tool = next(t for t in build_tools(db) if t.name == tool_name)
    for args in PROBES[tool_name]:
        out = _invoke(tool, args, PUBLIC)
        for value in args.values():  # the caller's own input may be echoed back ("No asset matching 'x'")
            out = out.replace(str(value), "<input>")
        leaked = [s for s in ORG_ONLY + ORG_ONLY_VALUES if s in out]
        assert not leaked, f"{tool_name}({args}) leaked {leaked}"


def test_tool_without_request_context_is_public_only(db):
    tool = next(t for t in build_tools(db) if t.name == "asset_overview")
    out = json.loads(tool.invoke({"asset": "Kestrel Bay Power Station"}))
    assert out["untrusted_data"]["found"] is False


def test_licensed_principal_sees_licensed_facts(db):
    assert q.asset_overview(db, "Kestrel Bay Power Station", LICENSED)["asset"]["capacity"] == 780.0
    ids = [i["incident_id"] for i in q.incident_history(db, "Harbor Point LNG", LICENSED)["incidents"]]
    assert "org:OUT-5001" in ids
    pub_ids = [i["incident_id"] for i in q.incident_history(db, "Harbor Point LNG", PUBLIC)["incidents"]]
    assert "org:OUT-5001" not in pub_ids and pub_ids


def test_entitlements_are_per_dataset(db):
    only_outages = ["public", "org:outages"]
    # the licensed outage on the org asset is visible, but the org asset itself is not
    assert q.asset_overview(db, "Kestrel Bay Power Station", only_outages)["found"] is False
    assert "org:OUT-5001" in json.dumps(q.incident_history(db, "Harbor Point LNG", only_outages))


def test_licensed_record_never_overwrites_public_properties(db):
    row = db.read("MATCH (a:Asset {asset_id: 'seed:harbor-lng'}) RETURN a.entitlement AS e, a.source AS s")[0]
    assert row == {"e": "public", "s": "seed"}


def test_graph_qa_refused_for_partial_entitlements(db):
    tool = next(t for t in build_tools(db) if t.name == "graph_qa")
    out = json.loads(_invoke(tool, {"question": "list assets"}, LICENSED))
    assert "only available to users entitled to all datasets" in out["untrusted_data"]["message"]


def test_similarity_never_mixes_entitlements(db):
    mixed = db.read("MATCH (a:Asset)-[s:SIMILAR_TO]->(b:Asset) WHERE a.entitlement <> b.entitlement "
                    "OR s.entitlement <> a.entitlement RETURN count(s) AS n")[0]["n"]
    assert mixed == 0
