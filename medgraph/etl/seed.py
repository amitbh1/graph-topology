"""Small offline sample graph for demos and tests — no network required.

ILLUSTRATIVE DATA ONLY. Vocabulary identifiers (MeSH UIs, ATC codes, ICD-10-CM
codes) are real, but tree numbers are abbreviated, label texts are short
paraphrases, and every adverse-event report and clinical trial is synthetic
(ids prefixed SEED-AE- / NCT-SEED-). Run `medgraph etl all` for real data.

The seed goes through the same load queries as the real ETL.
"""

from __future__ import annotations

from ..db import Neo4jClient
from ..schema import create_schema
from .atc import atc_parent, link_drugs_to_atc, write_atc
from .clinical_trials import LOAD_TRIAL, POST_LINKS
from .common import TermMatcher, affirmative_sentences, norm
from .faers import LOAD_REPORT, map_reactions
from .mesh import write_mesh
from .openfda_labels import LOAD_LABEL
from .similarity import compute_similarity

SOURCE = "seed (illustrative sample)"

# (ui, name, tree numbers, extra entry terms)
MESH = [
    ("D009750", "Nutritional and Metabolic Diseases", ["C18"], []),
    ("D008659", "Metabolic Diseases", ["C18.452"], []),
    ("D044882", "Glucose Metabolism Disorders", ["C18.452.394"], []),
    ("D003920", "Diabetes Mellitus", ["C18.452.394.750", "C19.246"], ["diabetes"]),
    ("D004700", "Endocrine System Diseases", ["C19"], []),
    ("D003924", "Diabetes Mellitus, Type 2", ["C18.452.394.750.149", "C19.246.300"],
     ["type 2 diabetes", "type 2 diabetes mellitus", "noninsulin-dependent diabetes mellitus", "niddm",
      "adult-onset diabetes mellitus"]),
    ("D003922", "Diabetes Mellitus, Type 1", ["C18.452.394.750.124", "C19.246.267"],
     ["type 1 diabetes", "type 1 diabetes mellitus", "insulin-dependent diabetes mellitus", "iddm",
      "juvenile-onset diabetes"]),
    ("D048909", "Diabetes Complications", ["C19.246.099"], ["diabetic complications"]),
    ("D003925", "Diabetic Angiopathies", ["C19.246.099.500"], []),
    ("D003930", "Diabetic Retinopathy", ["C19.246.099.500.382"], []),
    ("D017719", "Diabetic Foot", ["C19.246.099.500.191"], ["diabetic foot ulcer"]),
    ("D003928", "Diabetic Nephropathies", ["C19.246.099.750"], ["diabetic kidney disease"]),
    ("D007003", "Hypoglycemia", ["C18.452.394.984"], ["low blood sugar"]),
    ("D006943", "Hyperglycemia", ["C18.452.394.952"], ["high blood sugar", "elevated blood glucose"]),
    ("D014883", "Water-Electrolyte Imbalance", ["C18.452.950"], []),
    ("D007010", "Hyponatremia", ["C18.452.950.340"], ["low sodium"]),
    ("D002318", "Cardiovascular Diseases", ["C14"], []),
    ("D006331", "Heart Diseases", ["C14.280"], []),
    ("D006333", "Heart Failure", ["C14.280.434"], ["cardiac failure", "congestive heart failure"]),
    ("D014652", "Vascular Diseases", ["C14.907"], []),
    ("D006973", "Hypertension", ["C14.907.489"], ["high blood pressure"]),
    ("D006937", "Hypercholesterolemia", ["C18.452.584.500.396"], ["high cholesterol"]),
    ("D051436", "Renal Insufficiency, Chronic", ["C12.777.419.780.050"], ["chronic kidney disease"]),
    ("D009325", "Nausea", ["C23.888.821.712"], []),
    ("D014839", "Vomiting", ["C23.888.821.1000"], []),
    ("D003967", "Diarrhea", ["C23.888.821.214"], []),
    ("D006261", "Headache", ["C23.888.592.612.441"], []),
    ("D004244", "Dizziness", ["C23.888.592.958.250"], []),
    ("D063806", "Myalgia", ["C23.888.592.612.547"], []),
    ("D003371", "Cough", ["C23.888.852.293"], []),
    ("D013880", "Thirst", ["G07.203.650.917"], []),
]

ATC = [
    ("A", "ALIMENTARY TRACT AND METABOLISM"), ("A10", "DRUGS USED IN DIABETES"),
    ("A10B", "BLOOD GLUCOSE LOWERING DRUGS, EXCL. INSULINS"),
    ("A10BA", "Biguanides"), ("A10BA02", "metformin"),
    ("A10BB", "Sulfonylureas"), ("A10BB01", "glibenclamide"), ("A10BB07", "glipizide"),
    ("A10BB12", "glimepiride"),
    ("A10BH", "Dipeptidyl peptidase 4 (DPP-4) inhibitors"), ("A10BH01", "sitagliptin"),
    ("A10BK", "Sodium-glucose co-transporter 2 (SGLT2) inhibitors"),
    ("A10BK01", "dapagliflozin"), ("A10BK03", "empagliflozin"),
    ("C", "CARDIOVASCULAR SYSTEM"), ("C03", "DIURETICS"), ("C03X", "OTHER DIURETICS"),
    ("C03XA", "Vasopressin antagonists"), ("C03XA01", "tolvaptan"),
    ("C09", "AGENTS ACTING ON THE RENIN-ANGIOTENSIN SYSTEM"), ("C09A", "ACE INHIBITORS, PLAIN"),
    ("C09AA", "ACE inhibitors, plain"), ("C09AA03", "lisinopril"),
    ("C10", "LIPID MODIFYING AGENTS"), ("C10A", "LIPID MODIFYING AGENTS, PLAIN"),
    ("C10AA", "HMG CoA reductase inhibitors"), ("C10AA05", "atorvastatin"),
]

# drug, brands, other aliases, pharm class, manufacturer, indications text
DRUGS = [
    ("glipizide", ["Glucotrol", "Glucotrol XL"], [], "Sulfonylurea", "Example Labs",
     "Glipizide is indicated as an adjunct to diet and exercise to improve glycemic control in adults "
     "with type 2 diabetes mellitus. Not for treatment of type 1 diabetes mellitus or diabetic ketoacidosis."),
    ("glimepiride", ["Amaryl"], [], "Sulfonylurea", "Sample Pharma",
     "Glimepiride is indicated as an adjunct to diet and exercise to improve glycemic control in adults "
     "with type 2 diabetes mellitus. Not for use in type 1 diabetes."),
    ("glyburide", ["Diabeta", "Glynase"], ["glibenclamide"], "Sulfonylurea", "Example Labs",
     "Glyburide is indicated as an adjunct to diet and exercise to improve glycemic control in adults "
     "with noninsulin-dependent diabetes mellitus (NIDDM)."),
    ("metformin", ["Glucophage"], ["metformin hydrochloride"], "Biguanide", "Sample Pharma",
     "Metformin is indicated as an adjunct to diet and exercise to improve glycemic control in adults "
     "and pediatric patients 10 years and older with type 2 diabetes mellitus."),
    ("sitagliptin", ["Januvia"], [], "Dipeptidyl Peptidase 4 Inhibitor", "Demo Therapeutics",
     "Sitagliptin is indicated as an adjunct to diet and exercise to improve glycemic control in adults "
     "with type 2 diabetes. Not for patients with type 1 diabetes."),
    ("empagliflozin", ["Jardiance"], [], "Sodium-Glucose Cotransporter 2 Inhibitor", "Demo Therapeutics",
     "Empagliflozin is indicated to reduce the risk of cardiovascular death in adults with heart failure, "
     "and as an adjunct to diet and exercise to improve glycemic control in adults with type 2 diabetes. "
     "Also indicated in chronic kidney disease."),
    ("dapagliflozin", ["Farxiga"], [], "Sodium-Glucose Cotransporter 2 Inhibitor", "Sample Pharma",
     "Dapagliflozin is indicated in adults with type 2 diabetes, heart failure and chronic "
     "kidney disease."),
    ("tolvaptan", ["Samsca"], [], "Vasopressin V2 Receptor Antagonist", "Example Labs",
     "Tolvaptan is indicated for clinically significant hypervolemic and euvolemic hyponatremia, "
     "including patients with heart failure."),
    ("lisinopril", ["Zestril", "Prinivil"], [], "Angiotensin Converting Enzyme Inhibitor", "Sample Pharma",
     "Lisinopril is indicated for the treatment of hypertension, as adjunct therapy in heart failure, "
     "and in acute myocardial infarction."),
    ("atorvastatin", ["Lipitor"], [], "HMG-CoA Reductase Inhibitor", "Demo Therapeutics",
     "Atorvastatin is indicated to reduce LDL-C in adults with primary hypercholesterolemia and to reduce "
     "the risk of myocardial infarction in adults with diabetic kidney disease or high blood pressure."),
]

# drug -> list of (reactions, serious, death, hospitalization)
REPORTS = {
    "glipizide": [(["hypoglycaemia", "dizziness"], True, False, False),
                  (["hypoglycaemia", "blood glucose decreased"], True, False, False),
                  (["nausea"], False, False, False), (["diarrhoea", "nausea"], False, False, False),
                  (["headache"], False, False, False), (["hypoglycaemia", "confusional state"], True, False, False),
                  (["dizziness", "hyperhidrosis"], True, False, False)],
    "glimepiride": [(["hypoglycaemia"], True, False, True), (["dizziness", "nausea"], False, False, False),
                    (["hypoglycaemia", "headache"], True, False, False)],
    "glyburide": [(["hypoglycaemia"], True, False, True), (["nausea"], False, False, False)],
    "metformin": [(["diarrhoea", "nausea"], False, False, False), (["lactic acidosis"], True, False, True),
                  (["vomiting", "diarrhoea"], False, False, False)],
    "sitagliptin": [(["headache"], False, False, False), (["pancreatitis"], True, False, True)],
    "empagliflozin": [(["genital mycotic infection"], False, False, False), (["dizziness", "thirst"], False, False, False)],
    "dapagliflozin": [(["genital mycotic infection", "thirst"], False, False, False)],
    "tolvaptan": [(["thirst"], False, False, False), (["hepatic enzyme increased"], True, False, True)],
    "lisinopril": [(["cough"], False, False, False), (["dizziness", "headache"], False, False, False)],
    "atorvastatin": [(["myalgia"], False, False, False), (["myalgia", "nausea"], False, False, False)],
}

# nct, title, status, phases, sponsor, conditions, mesh uis, drugs
TRIALS = [
    ("NCT-SEED-001", "Sulfonylurea Versus DPP-4 Inhibitor Add-on in Type 2 Diabetes", "RECRUITING",
     ["PHASE4"], "Example University Hospital", ["Type 2 Diabetes"], ["D003924"], ["glipizide", "sitagliptin"]),
    ("NCT-SEED-002", "Glimepiride and Hypoglycemia Risk in Older Adults", "COMPLETED", ["PHASE4"],
     "Sample Pharma", ["Diabetes Mellitus, Type 2", "Hypoglycemia"], ["D003924", "D007003"], ["glimepiride"]),
    ("NCT-SEED-003", "SGLT2 Inhibition in Heart Failure With Diabetes", "ACTIVE_NOT_RECRUITING", ["PHASE3"],
     "Demo Therapeutics", ["Heart Failure", "Type 2 Diabetes"], ["D006333", "D003924"], ["empagliflozin"]),
    ("NCT-SEED-004", "Wound Healing Strategies for Diabetic Foot Ulcer", "RECRUITING", ["NA"],
     "Example University Hospital", ["Diabetic Foot Ulcer"], ["D017719"], []),
    ("NCT-SEED-005", "Screening for Diabetic Retinopathy With Retinal Imaging", "NOT_YET_RECRUITING", ["NA"],
     "Regional Eye Institute", ["Diabetic Retinopathy"], ["D003930"], []),
    ("NCT-SEED-006", "Metformin in Type 1 Diabetes Adolescents", "COMPLETED", ["PHASE2"],
     "Children's Research Network", ["Type 1 Diabetes"], ["D003922"], ["metformin"]),
    ("NCT-SEED-007", "ACE Inhibitor Titration in Resistant Hypertension", "RECRUITING", ["PHASE4"],
     "Sample Pharma", ["Hypertension"], ["D006973"], ["lisinopril"]),
    ("NCT-SEED-008", "Dapagliflozin in Chronic Kidney Disease", "COMPLETED", ["PHASE3"],
     "Demo Therapeutics", ["Chronic Kidney Disease"], ["D051436"], ["dapagliflozin"]),
]

ICD10 = [
    ("D003924", "E11.9", "Type 2 diabetes mellitus without complications"),
    ("D003922", "E10.9", "Type 1 diabetes mellitus without complications"),
    ("D003930", "E11.319", "Type 2 diabetes mellitus with unspecified diabetic retinopathy without macular edema"),
    ("D017719", "E11.621", "Type 2 diabetes mellitus with foot ulcer"),
    ("D007003", "E16.2", "Hypoglycemia, unspecified"),
    ("D007010", "E87.1", "Hypo-osmolality and hyponatremia"),
    ("D006333", "I50.9", "Heart failure, unspecified"),
    ("D006973", "I10", "Essential (primary) hypertension"),
    ("D006937", "E78.00", "Pure hypercholesterolemia, unspecified"),
    ("D051436", "N18.9", "Chronic kidney disease, unspecified"),
]


def mesh_records() -> list[dict]:
    return [{
        "ui": ui, "name": name, "scope_note": "", "tree_numbers": trees,
        "is_disease": any(t.startswith(("C", "F03")) for t in trees),
        "terms": sorted({name, *extra}),
    } for ui, name, trees, extra in MESH]


def label_rows() -> list[dict]:
    disease_terms = [(norm(t), r["ui"]) for r in mesh_records() if r["is_disease"] for t in r["terms"]]
    matcher = TermMatcher(disease_terms)
    rows = []
    for drug, brands, others, pclass, mfr, text in DRUGS:
        aliases = [{"name": norm(b), "kind": "brand", "display": b} for b in brands]
        aliases += [{"name": norm(o), "kind": "generic", "display": o} for o in others]
        rows.append({
            "drug": drug, "drug_display": drug.title(), "rxcui": None,
            "label_id": f"seed-label-{drug}", "set_id": None,
            "title": f"{brands[0]} ({drug})", "indications": text, "adverse_reactions": "",
            "effective_time": None, "route": "ORAL", "manufacturer": mfr, "pharm_classes": [pclass],
            "aliases": aliases, "source": SOURCE,
            "indication_terms": [{"name": t, "ui": ui} for t, ui in matcher.find(affirmative_sentences(text))],
        })
    return rows


def report_rows() -> list[dict]:
    rows, n = [], 0
    for drug, reports in REPORTS.items():
        for reactions, serious, death, hosp in reports:
            n += 1
            rows.append({"report_id": f"SEED-AE-{n:04d}", "serious": serious, "death": death,
                         "hospitalization": hosp, "receive_date": None, "country": None,
                         "suspects": [drug], "reactions": reactions, "source": SOURCE})
    return rows


def trial_rows() -> list[dict]:
    return [{"nct_id": nct, "title": title, "status": status, "start_date": None, "phases": phases,
             "study_type": "INTERVENTIONAL", "enrollment": None, "sponsor": sponsor,
             "conditions": [{"name": norm(c), "display": c} for c in conds],
             "mesh_uis": uis, "drugs": drugs, "source": SOURCE}
            for nct, title, status, phases, sponsor, conds, uis, drugs in TRIALS]


def atc_rows() -> list[dict]:
    from .atc import atc_level
    return [{"code": c, "name": n, "level": atc_level(c), "parent": atc_parent(c)} for c, n in ATC]


def load_seed(client: Neo4jClient, embed: bool = True) -> None:
    create_schema(client)
    write_mesh(client, mesh_records())
    write_atc(client, atc_rows())
    client.write_batches(LOAD_LABEL, label_rows())
    link_drugs_to_atc(client)
    client.write_batches(LOAD_REPORT, report_rows())
    map_reactions(client)
    client.write_batches(LOAD_TRIAL, trial_rows())
    for q in POST_LINKS:
        client.write(q)
    client.write_batches(
        "UNWIND $rows AS r MATCH (m:MeshDescriptor {ui: r.ui}) "
        "MERGE (c:ICD10Code {code: r.code}) SET c.name = r.name MERGE (m)-[:HAS_ICD10]->(c)",
        [{"ui": u, "code": c, "name": n} for u, c, n in ICD10],
    )
    compute_similarity(client)
    if embed:
        from .embed import embed_all
        embed_all(client)
    print("seed: illustrative sample graph loaded")
