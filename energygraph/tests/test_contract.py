"""Ontology contract, canonical records, connectors and governance — no database needed."""

import datetime as dt
import io
import json

import pytest
from pydantic import ValidationError

from energygraph.etl.loader import LoadRejected, check_governance
from energygraph.ontology import extract_cypher_identifiers, get_ontology
from energygraph.schema import vector_index_statements
from energygraph.sources.base import registry
from energygraph.sources.organization.connector import (DATASETS, FileTransport, HttpTransport, OrganizationSource,
                                                        validate_file, validate_record)
from energygraph.sources.public.gleif import best_match, legal_key
from energygraph.sources.public.gppd import transform_row
from energygraph.sources.public.oeo import parse_oeo
from energygraph.sources.public.wikidata import transform_bindings
from energygraph.sources.records import AssetRecord, IncidentRecord
from energygraph.validate import check_source

SAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "energygraph" / "sources" / "organization" / "samples"


# --------------------------------------------------------------------------- ontology
def test_ontology_is_valid_and_complete():
    onto = get_ontology()
    assert onto.validate() == []
    assert len(onto.classes) == 18 and len(onto.relationships) == 22
    assert set(onto.asset_types) == {"PowerPlant", "Refinery", "LNGTerminal", "Pipeline", "Field", "StorageSite"}
    assert len(onto.root_classes) == 12


def test_all_cypher_in_code_uses_declared_schema():
    assert check_source(get_ontology()) == []


def test_undeclared_schema_is_caught():
    assert get_ontology().check_cypher("MATCH (c:Company)-[:OWNES]->(a:Asset) RETURN a") == [
        "undeclared relationship type :OWNES"]
    labels, rels = extract_cypher_identifiers("MATCH (a:Asset)-[:OWNS|OPERATES*1..3]->(b)")
    assert labels == {"Asset"} and rels == {"OWNS", "OPERATES"}


def test_constraints_and_vector_indexes_come_from_ontology():
    onto = get_ontology()
    stmts = onto.constraint_statements()
    assert any("(n:Asset) REQUIRE n.asset_id IS UNIQUE" in s for s in stmts)
    assert not any("(n:PowerPlant)" in s for s in stmts)  # subclasses share the Asset constraint
    assert len(vector_index_statements(onto, 768)) == len(onto.embedded_classes) == 11


# --------------------------------------------------------------------------- records
def test_records_validate_strictly():
    with pytest.raises(ValidationError):
        AssetRecord(source="gppd", asset_id="gppd:X1", asset_type="Windmill", name="x")
    with pytest.raises(ValidationError):
        AssetRecord(source="gppd", asset_id="gppd:X1", asset_type="PowerPlant", name="x", capacity=10)  # no unit
    with pytest.raises(ValidationError):
        AssetRecord(source="gppd", asset_id="bad id", asset_type="PowerPlant", name="x")
    with pytest.raises(ValidationError):
        AssetRecord(source="gppd", asset_id="gppd:X1", asset_type="PowerPlant", name="x", extra_field=1)
    with pytest.raises(ValidationError):
        IncidentRecord(source="seed", incident_id="seed:i", asset_id="seed:a", incident_type="safety",
                       start=dt.date(2025, 2, 1), end=dt.date(2025, 1, 1))
    with pytest.raises(ValidationError):
        AssetRecord(source="gppd", entitlement="secret", asset_id="gppd:X1", asset_type="PowerPlant", name="x")


# --------------------------------------------------------------------------- public connectors
def test_gppd_row_transform():
    row = {"country": "USA", "country_long": "United States of America", "name": "Example Plant",
           "gppd_idnr": "USA0001234", "capacity_mw": "820.5", "latitude": "29.7", "longitude": "-93.9",
           "primary_fuel": "Gas", "other_fuel1": "Oil", "other_fuel2": "", "other_fuel3": "",
           "commissioning_year": "2011.0", "owner": "Example Power Co", "year_of_capacity_data": "2019"}
    asset, company = transform_row(row)
    assert asset.asset_id == "gppd:USA0001234" and asset.capacity == 820.5 and asset.capacity_unit == "MW"
    assert asset.concepts == ["natural gas", "crude oil"]
    assert asset.commissioning_year == 2011 and asset.as_of == dt.date(2019, 12, 31)
    assert company.company_id == "name:example-power-co" and asset.owners[0].company_id == company.company_id


def test_wikidata_transform_skips_unlabeled_and_links_operators():
    b = [{"item": {"value": "http://www.wikidata.org/entity/Q1"}, "itemLabel": {"value": "Example Refinery"},
          "iso3": {"value": "GBR"}, "countryLabel": {"value": "United Kingdom"},
          "coord": {"value": "Point(0.5 51.5)"}, "inception": {"value": "1972-01-01T00:00:00Z"},
          "operator": {"value": "http://www.wikidata.org/entity/Q9"}, "operatorLabel": {"value": "Example Oil"}},
         {"item": {"value": "http://www.wikidata.org/entity/Q2"}, "itemLabel": {"value": "Q2"}}]
    recs = list(transform_bindings("Refinery", b))
    assets = [r for r in recs if r.kind == "asset"]
    assert len(assets) == 1 and assets[0].latitude == 51.5 and assets[0].operator_id == "wd:Q9"
    assert assets[0].commissioning_year == 1972


def test_gleif_matching_is_exact_after_suffix_removal():
    assert legal_key("Cheniere Energy, Inc.") == legal_key("CHENIERE ENERGY INC") == "cheniere energy"
    cands = [{"attributes": {"value": "CHENIERE ENERGY PARTNERS, L.P."},
              "relationships": {"lei-records": {"data": {"id": "5493005UEC8AZ34LDV29"}}}},
             {"attributes": {"value": "CHENIERE ENERGY, INC."},
              "relationships": {"lei-records": {"data": {"id": "MIHC87W9WTYSYZWV1J40"}}}}]
    assert best_match("Cheniere Energy Inc", cands) == ("MIHC87W9WTYSYZWV1J40", "CHENIERE ENERGY, INC.")
    assert best_match("Cheniere", cands) is None  # never guess an ownership link


def test_oeo_parser(tmp_path):
    owl = tmp_path / "mini.owl"
    owl.write_text("""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
 xmlns:owl="http://www.w3.org/2002/07/owl#" xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#"
 xmlns:obo="http://purl.obolibrary.org/obo/">
 <owl:Class rdf:about="https://openenergyplatform.org/ontology/oeo/OEO_00000173"><rdfs:label>fuel</rdfs:label></owl:Class>
 <owl:Class rdf:about="https://openenergyplatform.org/ontology/oeo/OEO_00000292">
   <rdfs:label>natural gas</rdfs:label>
   <rdfs:subClassOf rdf:resource="https://openenergyplatform.org/ontology/oeo/OEO_00000173"/>
   <oboInOwl:hasExactSynonym>fossil gas</oboInOwl:hasExactSynonym>
   <obo:IAO_0000115>A gaseous fossil fuel.</obo:IAO_0000115>
 </owl:Class>
</rdf:RDF>""", encoding="utf-8")
    rows = {r["code"]: r for r in parse_oeo(owl)}
    assert rows["oeo:OEO_00000292"]["parents"] == ["oeo:OEO_00000173"]
    assert rows["oeo:OEO_00000292"]["synonyms"] == ["fossil gas"]
    assert rows["oeo:OEO_00000292"]["definition"] == "A gaseous fossil fuel."


def test_eia_is_skipped_without_key(monkeypatch):
    ok, reason = registry()["eia"].available()
    assert ok or "EIA_API_KEY" in reason


# --------------------------------------------------------------------------- organization-source
def test_org_record_entitlement_is_imposed_not_trusted():
    rec = validate_record({"kind": "incident", "incident_id": "org:X", "asset_id": "seed:a",
                           "incident_type": "safety", "start": "2025-01-01"}, dataset="outages",
                          source_id="org-outages")
    assert rec.entitlement == "org:outages" and rec.source == "org-outages"
    with pytest.raises(ValueError, match="may not set entitlement"):
        validate_record({"kind": "incident", "incident_id": "org:X", "asset_id": "seed:a", "incident_type": "safety",
                         "start": "2025-01-01", "entitlement": "public"}, dataset="outages", source_id="org-outages")
    with pytest.raises(ValueError, match="not allowed"):
        validate_record({"kind": "asset", "asset_id": "org:A", "asset_type": "PowerPlant", "name": "x"},
                        dataset="outages", source_id="org-outages")


def test_sample_payloads_pass_contract_except_deliberate_bad_rows():
    results = {p.stem: validate_file(p) for p in SAMPLES.glob("*.jsonl")}
    assert set(results) <= set(DATASETS)
    assert results["power-assets"]["invalid"] == 1 and results["outages"]["invalid"] == 2
    assert all(r["invalid"] == 0 for k, r in results.items() if k not in ("power-assets", "outages"))


def test_file_transport_flags_non_json(tmp_path):
    (tmp_path / "news.jsonl").write_text('{"kind": "document"\nnot json\n', encoding="utf-8")
    conn = OrganizationSource("news", FileTransport(tmp_path))
    assert list(conn.fetch()) == [] and len(conn.rejected) == 2


def test_http_transport_requires_https():
    with pytest.raises(ValueError):
        HttpTransport("http://org.example", "t", 5)


def test_http_transport_pagination(monkeypatch):
    t = HttpTransport("https://org.example", "token", 5)
    pages = iter([{"records": [{"a": 1}], "next_cursor": "c2"}, {"records": [{"a": 2}], "next_cursor": None}])
    monkeypatch.setattr(t, "_get", lambda url, params: next(pages))
    assert [p for p in t.pages("news", 1)] == [[{"a": 1}], [{"a": 2}]]
    assert t.session.headers["Authorization"] == "Bearer token"


def test_governance_rejects_cross_entitlement_and_unregistered():
    metas = {sid: c.meta for sid, c in registry().items()}
    pub = AssetRecord(source="gppd", entitlement="org:power-assets", asset_id="gppd:X", asset_type="PowerPlant", name="x")
    with pytest.raises(LoadRejected, match="may not emit entitlement"):
        check_governance(pub, metas)
    rogue = AssetRecord(source="rogue", asset_id="gppd:X", asset_type="PowerPlant", name="x")
    with pytest.raises(LoadRejected, match="not registered"):
        check_governance(rogue, metas)
    wrong_kind = IncidentRecord(source="gppd", incident_id="gppd:i", asset_id="gppd:a", incident_type="safety",
                                start=dt.date(2025, 1, 1))
    with pytest.raises(LoadRejected, match="may not emit incident"):
        check_governance(wrong_kind, metas)
