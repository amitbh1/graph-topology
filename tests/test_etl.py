import io

from medgraph.etl.atc import atc_level, atc_parent, atc_path
from medgraph.etl.clinical_trials import transform_study
from medgraph.etl.common import TermMatcher, affirmative_sentences, americanize, norm
from medgraph.etl.faers import transform_report
from medgraph.etl.mesh import hierarchy_pairs, parse_mesh_xml
from medgraph.etl.seed import atc_rows, label_rows, mesh_records
from medgraph.etl.similarity import similarity_score


def test_atc_levels_and_path():
    assert [atc_level(c) for c in ("C", "C03", "C03X", "C03XA", "C03XA01")] == [1, 2, 3, 4, 5]
    assert atc_parent("A10BB07") == "A10BB"
    assert atc_parent("A") is None
    assert atc_path("A10BB07") == ["A", "A10", "A10B", "A10BB", "A10BB07"]


MESH_XML = b"""<?xml version="1.0"?>
<DescriptorRecordSet>
 <DescriptorRecord><DescriptorUI>D009750</DescriptorUI><DescriptorName><String>Nutritional and Metabolic Diseases</String></DescriptorName>
  <TreeNumberList><TreeNumber>C18</TreeNumber></TreeNumberList>
  <ConceptList><Concept PreferredConceptYN="Y"><TermList><Term><String>Nutritional and Metabolic Diseases</String></Term></TermList></Concept></ConceptList>
 </DescriptorRecord>
 <DescriptorRecord><DescriptorUI>D004700</DescriptorUI><DescriptorName><String>Endocrine System Diseases</String></DescriptorName>
  <TreeNumberList><TreeNumber>C19</TreeNumber></TreeNumberList><ConceptList/>
 </DescriptorRecord>
 <DescriptorRecord><DescriptorUI>D008659</DescriptorUI><DescriptorName><String>Metabolic Diseases</String></DescriptorName>
  <TreeNumberList><TreeNumber>C18.452</TreeNumber></TreeNumberList><ConceptList/>
 </DescriptorRecord>
 <DescriptorRecord><DescriptorUI>D003920</DescriptorUI><DescriptorName><String>Diabetes Mellitus</String></DescriptorName>
  <PharmacologicalActionList><PharmacologicalAction><DescriptorReferredTo><DescriptorUI>D999999</DescriptorUI></DescriptorReferredTo></PharmacologicalAction></PharmacologicalActionList>
  <TreeNumberList><TreeNumber>C18.452.394</TreeNumber><TreeNumber>C19.246</TreeNumber></TreeNumberList>
  <ConceptList><Concept PreferredConceptYN="Y"><ScopeNote>A heterogeneous group of disorders.</ScopeNote>
   <TermList><Term><String>Diabetes Mellitus</String></Term><Term><String>Diabetes</String></Term></TermList></Concept></ConceptList>
 </DescriptorRecord>
</DescriptorRecordSet>"""


def test_parse_mesh_xml_and_multi_parent_hierarchy():
    recs = list(parse_mesh_xml(io.BytesIO(MESH_XML)))
    dm = next(r for r in recs if r["ui"] == "D003920")
    assert dm["terms"] == ["Diabetes", "Diabetes Mellitus"]
    assert dm["scope_note"].startswith("A heterogeneous")
    assert dm["is_disease"]
    pairs = hierarchy_pairs(recs)
    # C18.452.394 -> C18.452 (Metabolic Diseases); C19.246 -> C19 (Endocrine System Diseases)
    assert ("D003920", "D008659") in pairs
    assert ("D003920", "D004700") in pairs
    assert ("D008659", "D009750") in pairs


def test_term_matcher_prefers_longest_match():
    m = TermMatcher([("diabetes mellitus", "D003920"), ("type 2 diabetes mellitus", "D003924")])
    found = dict(m.find("Improve glycemic control in adults with Type 2 Diabetes Mellitus."))
    assert found == {"type 2 diabetes mellitus": "D003924"}


def test_americanize_meddra_spelling():
    assert americanize("hypoglycaemia") == "hypoglycemia"
    assert americanize("diarrhoea") == "diarrhea"
    assert americanize("oedema peripheral") == "edema peripheral"
    assert americanize("haemorrhage") == "hemorrhage"


def test_transform_report_links_only_suspect_drugs():
    rep = {"safetyreportid": "123", "serious": "1", "seriousnessdeath": "1",
           "patient": {"drug": [
               {"drugcharacterization": "1", "openfda": {"generic_name": ["GLIPIZIDE"]}},
               {"drugcharacterization": "2", "openfda": {"generic_name": ["METFORMIN HYDROCHLORIDE"]}}],
               "reaction": [{"reactionmeddrapt": "Hypoglycaemia"}, {"reactionmeddrapt": "Hypoglycaemia"}]}}
    row = transform_report(rep, "glipizide")
    assert row["suspects"] == ["glipizide"]
    assert row["reactions"] == ["hypoglycaemia"]
    assert row["serious"] and row["death"] and not row["hospitalization"]


def test_transform_study_v2():
    study = {"protocolSection": {
        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "A trial"},
        "statusModule": {"overallStatus": "RECRUITING"},
        "designModule": {"phases": ["PHASE3"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Uni"}},
        "conditionsModule": {"conditions": ["Type 2 Diabetes"]},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Glipizide"},
                                                      {"type": "BEHAVIORAL", "name": "Diet"}]}},
        "derivedSection": {"conditionBrowseModule": {"meshes": [{"id": "D003924", "term": "Diabetes Mellitus, Type 2"}]}}}
    row = transform_study(study)
    assert row["nct_id"] == "NCT01234567"
    assert row["drugs"] == ["glipizide"]
    assert row["mesh_uis"] == ["D003924"]
    assert row["conditions"] == [{"name": "type 2 diabetes", "display": "Type 2 Diabetes"}]


def test_similarity_formula():
    assert similarity_score(shared_indications=3, shared_reactions=4) == 10


def test_seed_is_self_consistent():
    uis = {r["ui"] for r in mesh_records()}
    for row in label_rows():
        assert row["indication_terms"], row["drug"]
        assert all(t["ui"] in uis for t in row["indication_terms"])
    glipizide = next(r for r in label_rows() if r["drug"] == "glipizide")
    assert {"glucotrol", "glucotrol xl"} <= {a["name"] for a in glipizide["aliases"]}
    codes = {r["code"] for r in atc_rows()}
    assert all(r["parent"] in codes for r in atc_rows() if r["parent"])


def test_norm():
    assert norm("  Type 2   Diabetes ") == "type 2 diabetes"


def test_negated_indications_are_dropped():
    text = "Indicated for type 2 diabetes mellitus. Not for treatment of type 1 diabetes mellitus."
    m = TermMatcher([("type 2 diabetes mellitus", "D003924"), ("type 1 diabetes mellitus", "D003922")])
    assert dict(m.find(affirmative_sentences(text))) == {"type 2 diabetes mellitus": "D003924"}
