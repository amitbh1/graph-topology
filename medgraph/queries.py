"""Deterministic graph queries. Shared by the API, dashboard and assistant tools.

Every function returns plain JSON-serialisable data taken from the graph. When
the graph has no answer, the result says so (`found: False`) instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any

from .db import Neo4jClient
from .etl.common import norm
from .ontology import get_ontology
from .schema import vector_index_name

ATC_CODE_RE = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")
SAFETY_NOTE = ("FAERS reports are reported associations from a sample of reports; they do not "
               "prove that the drug caused the reaction and are not a measure of overall safety.")
SIMILARITY_NOTE = ("Exploratory score = shared_indications*2 + shared_reactions. Not a clinical "
                   "recommendation and not evidence that drugs are interchangeable.")


def _lucene_phrase(term: str) -> str:
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lucene_fuzzy(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    return " AND ".join(f"{w}~" if len(w) > 4 else w for w in words) or '""'


# --------------------------------------------------------------------------- resolution
def resolve_drug(client: Neo4jClient, name: str) -> dict | None:
    """Brand name / alias / generic -> canonical Drug."""
    n = norm(name)
    rows = client.read(
        "MATCH (d:Drug {name: $n}) RETURN d.name AS drug, 'exact' AS via, null AS alias "
        "UNION "
        "MATCH (a:DrugAlias {name: $n})-[:ALIAS_OF]->(d:Drug) "
        "RETURN d.name AS drug, 'alias:' + coalesce(a.kind, 'alias') AS via, a.display AS alias",
        n=n,
    )
    if rows:
        return {"input": name, **rows[0]}
    if not n:
        return None
    rows = client.read(
        "CALL db.index.fulltext.queryNodes('entity_names', $q) YIELD node, score "
        "WHERE node:Drug OR node:DrugAlias "
        "OPTIONAL MATCH (node)-[:ALIAS_OF]->(target:Drug) "
        "RETURN coalesce(target.name, node.name) AS drug, node.display AS matched, score "
        "ORDER BY score DESC LIMIT 1",
        q=_lucene_fuzzy(name),
    )
    if rows:
        return {"input": name, "drug": rows[0]["drug"], "via": "fuzzy", "alias": rows[0]["matched"]}
    return None


def resolve_concept(client: Neo4jClient, term: str) -> dict | None:
    """Free-text disease / concept -> MeSH descriptor (exact synonym, then fuzzy)."""
    n = norm(term)
    rows = client.read(
        "MATCH (d:MeshDescriptor)-[:HAS_ENTRY_TERM]->(:MeshEntryTerm {name: $n}) "
        "RETURN d.ui AS ui, d.name AS name, 'synonym' AS via "
        "ORDER BY d.is_disease DESC, CASE WHEN toLower(d.name) = $n THEN 0 ELSE 1 END LIMIT 1",
        n=n,
    )
    if not rows and n:
        rows = client.read(
            "CALL db.index.fulltext.queryNodes('entity_names', $q) YIELD node, score "
            "WHERE node:MeshEntryTerm "
            "MATCH (d:MeshDescriptor)-[:HAS_ENTRY_TERM]->(node) "
            "RETURN d.ui AS ui, d.name AS name, 'fuzzy:' + node.display AS via "
            "ORDER BY score DESC, d.is_disease DESC LIMIT 1",
            q=_lucene_fuzzy(term),
        )
    return {"input": term, **rows[0]} if rows else None


def _not_found(kind: str, value: str) -> dict:
    return {"found": False, "message": f"No {kind} matching '{value}' exists in the MedGraph graph."}


# --------------------------------------------------------------------------- vocabulary
def mesh_expand(client: Neo4jClient, term: str, max_depth: int = 10, max_terms: int = 2000) -> dict:
    concept = resolve_concept(client, term)
    if not concept:
        return _not_found("MeSH concept", term)
    depth = max(0, min(int(max_depth), 20))
    rows = client.read(
        f"MATCH (root:MeshDescriptor {{ui: $ui}})<-[:MESH_PARENT*0..{depth}]-(c:MeshDescriptor) "
        "WITH DISTINCT c "
        "OPTIONAL MATCH (c)-[:HAS_ENTRY_TERM]->(t:MeshEntryTerm) "
        "RETURN c.ui AS ui, c.name AS name, collect(t.name) AS terms",
        ui=concept["ui"],
    )
    parents = client.read(
        "MATCH (:MeshDescriptor {ui: $ui})-[:MESH_PARENT]->(p:MeshDescriptor) RETURN p.ui AS ui, p.name AS name",
        ui=concept["ui"],
    )
    terms = sorted({t for r in rows for t in r["terms"]} | {norm(r["name"]) for r in rows})
    return {
        "found": True,
        "concept": concept,
        "broader": parents,
        "narrower": sorted((r["name"] for r in rows if r["ui"] != concept["ui"]))[:100],
        "descriptor_count": len(rows),
        "term_count": len(terms),
        "terms": terms[:max_terms],
    }


def decode_atc(client: Neo4jClient, drug_or_code: str) -> dict:
    text = drug_or_code.strip()
    if ATC_CODE_RE.match(text.upper()) and len(text) in (1, 3, 4, 5, 7):
        codes, drug = [text.upper()], None
    else:
        drug = resolve_drug(client, text)
        if not drug:
            return _not_found("drug", text)
        codes = [r["code"] for r in client.read(
            "MATCH (:Drug {name: $d})-[:CLASSIFIED_AS]->(a:ATCLevel) RETURN a.code AS code ORDER BY code",
            d=drug["drug"])]
        if not codes:
            return {"found": False, "drug": drug,
                    "message": f"'{drug['drug']}' is in the graph but has no ATC classification."}
    out = []
    for code in codes:
        path = client.read(
            "MATCH p = (a:ATCLevel {code: $c})-[:ATC_PARENT*0..4]->(top:ATCLevel) "
            "WHERE NOT (top)-[:ATC_PARENT]->() "
            "RETURN [n IN reverse(nodes(p)) | {code: n.code, name: n.name, level: n.level}] AS path",
            c=code,
        )
        if not path:
            continue
        group = client.read(
            "MATCH (:ATCLevel {code: $c})-[:ATC_PARENT]->(g:ATCLevel)<-[:ATC_PARENT]-(s:ATCLevel) "
            "WHERE s.code <> $c "
            "OPTIONAL MATCH (s)<-[:CLASSIFIED_AS]-(d:Drug) "
            "RETURN s.code AS code, s.name AS name, d.name AS drug_in_graph ORDER BY code LIMIT 25",
            c=code,
        )
        out.append({"code": code, "path": path[0]["path"], "same_group": group})
    if not out:
        return _not_found("ATC code", text)
    return {"found": True, "drug": drug, "classifications": out}


# --------------------------------------------------------------------------- documents
def search_documents(client: Neo4jClient, term: str, expand: bool = True, limit: int = 10) -> dict:
    """Keyword search over openFDA labels, optionally expanded through the MeSH hierarchy."""
    baseline_terms = [norm(term)]
    expansion = mesh_expand(client, term, max_terms=1000) if expand else {"found": False}
    terms = expansion["terms"] if expansion.get("found") else baseline_terms

    def run(ts: list[str], k: int | None) -> list[dict]:
        q = " OR ".join(_lucene_phrase(t) for t in ts[:1000])
        return client.read(
            "CALL db.index.fulltext.queryNodes('druglabel_text', $q) YIELD node, score "
            "OPTIONAL MATCH (d:Drug)-[:HAS_LABEL]->(node) "
            "RETURN node.label_id AS label_id, node.title AS title, d.name AS drug, "
            "       node.indications AS indications, node.source AS source, score "
            "ORDER BY score DESC" + (" LIMIT $k" if k else ""),
            q=q, k=k,
        )

    baseline = run(baseline_terms, None)
    expanded = run(terms, None)
    docs = []
    for d in expanded[:limit]:
        text = (d.pop("indications") or "").lower()
        d["matched_terms"] = [t for t in terms if t in text][:10]
        d["snippet"] = text[:300]
        docs.append(d)
    gain = len(expanded) - len(baseline)
    return {
        "found": bool(expanded),
        "query": term,
        "expanded": bool(expansion.get("found")),
        "concept": expansion.get("concept"),
        "expanded_term_count": len(terms),
        "baseline_matches": len(baseline),
        "expanded_matches": len(expanded),
        "recall_gain_pct": round(100 * gain / len(baseline), 1) if baseline else None,
        "documents": docs,
    }


# --------------------------------------------------------------------------- safety
def drug_safety(client: Neo4jClient, drug: str, limit: int = 15) -> dict:
    resolved = resolve_drug(client, drug)
    if not resolved:
        return _not_found("drug", drug)
    d = resolved["drug"]
    summary = client.read(
        "MATCH (d:Drug {name: $d}) OPTIONAL MATCH (d)-[:SUSPECT_IN]->(e:AdverseEventReport) "
        "WITH collect(e) AS reps "
        "RETURN size(reps) AS reports, size([e IN reps WHERE e.serious]) AS serious, "
        "       size([e IN reps WHERE e.death]) AS deaths, "
        "       size([e IN reps WHERE e.hospitalization]) AS hospitalizations",
        d=d,
    )[0]
    reactions = client.read(
        "MATCH (:Drug {name: $d})-[:SUSPECT_IN]->(e:AdverseEventReport)-[:HAS_REACTION]->(r:Reaction) "
        "OPTIONAL MATCH (r)-[:REACTION_MAPS_TO]->(m:MeshDescriptor) "
        "RETURN r.name AS reaction, count(DISTINCT e) AS reports, "
        "       collect(DISTINCT m.name) AS mesh_concepts, collect(DISTINCT e.report_id)[..5] AS report_ids "
        "ORDER BY reports DESC, reaction LIMIT $k",
        d=d, k=limit,
    )
    distinct = client.read(
        "MATCH (:Drug {name: $d})-[:SUSPECT_IN]->()-[:HAS_REACTION]->(r:Reaction) "
        "RETURN count(DISTINCT r) AS n", d=d)[0]["n"]
    return {"found": True, "drug": resolved, **summary, "distinct_reactions": distinct,
            "top_reactions": reactions, "note": SAFETY_NOTE}


# --------------------------------------------------------------------------- trials
def trials_pipeline(client: Neo4jClient, condition: str | None = None, drug: str | None = None,
                    status: str | None = None, limit: int = 20) -> dict:
    if not condition and not drug:
        return {"found": False, "message": "Provide a condition or a drug."}
    params: dict[str, Any] = {"status": status.upper() if status else None, "k": limit}
    if condition:
        concept = resolve_concept(client, condition)
        if not concept:
            return _not_found("condition", condition)
        params["ui"] = concept["ui"]
        match = ("MATCH (root:MeshDescriptor {ui: $ui})<-[:MESH_PARENT*0..10]-(m:MeshDescriptor) "
                 "WITH DISTINCT m "
                 "MATCH (t:ClinicalTrial)-[:STUDIES_CONDITION]->(:Condition)-[:CONDITION_MAPS_TO]->(m) ")
        subject: dict = {"condition": concept}
    else:
        resolved = resolve_drug(client, drug)
        if not resolved:
            return _not_found("drug", drug)
        params["d"] = resolved["drug"]
        match = ("MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(:Intervention)"
                 "-[:INTERVENTION_IS]->(:Drug {name: $d}) ")
        subject = {"drug": resolved}
    base = match + "WITH DISTINCT t WHERE $status IS NULL OR t.status = $status "
    trials = client.read(
        base +
        "OPTIONAL MATCH (t)-[:SPONSORED_BY]->(s:Sponsor) "
        "OPTIONAL MATCH (t)-[:STUDIES_CONDITION]->(c:Condition) "
        "OPTIONAL MATCH (t)-[:TESTS_INTERVENTION]->(iv:Intervention) "
        "RETURN t.nct_id AS nct_id, t.title AS title, t.status AS status, t.phases AS phases, "
        "       s.display AS sponsor, collect(DISTINCT c.display) AS conditions, "
        "       collect(DISTINCT iv.name) AS interventions, t.source AS source "
        "ORDER BY t.status, t.nct_id LIMIT $k",
        **params,
    )
    by_status = client.read(base + "RETURN t.status AS status, count(*) AS trials ORDER BY trials DESC",
                            **params)
    sponsors = client.read(
        base + "MATCH (t)-[:SPONSORED_BY]->(s:Sponsor) "
        "RETURN s.display AS sponsor, count(*) AS trials ORDER BY trials DESC LIMIT 10", **params)
    total = sum(r["trials"] for r in by_status)
    return {"found": total > 0, **subject, "total_trials": total, "by_status": by_status,
            "top_sponsors": sponsors, "trials": trials,
            **({} if total else {"message": "The graph contains no trials for this query."})}


# --------------------------------------------------------------------------- analytics
def similar_drugs(client: Neo4jClient, drug: str, limit: int = 10) -> dict:
    resolved = resolve_drug(client, drug)
    if not resolved:
        return _not_found("drug", drug)
    rows = client.read(
        "MATCH (d:Drug {name: $d})-[s:SIMILAR_TO]-(o:Drug) "
        "RETURN o.name AS drug, s.score AS score, s.shared_indications AS shared_indications, "
        "       s.shared_reactions AS shared_reactions, "
        "       [(d)-[:INDICATED_FOR]->(i:IndicationTerm)<-[:INDICATED_FOR]-(o) | i.name][..8] AS indications_in_common "
        "ORDER BY score DESC, drug LIMIT $k",
        d=resolved["drug"], k=limit,
    )
    total = client.read("MATCH (:Drug {name: $d})-[s:SIMILAR_TO]-() RETURN count(s) AS n",
                        d=resolved["drug"])[0]["n"]
    return {"found": True, "drug": resolved, "total_similar": total, "similar": rows,
            "note": SIMILARITY_NOTE}


def disease_overview(client: Neo4jClient, disease: str, limit: int = 15) -> dict:
    """Deterministic multi-step answer: concept -> narrower concepts -> drugs, reactions, trials, ICD-10."""
    concept = resolve_concept(client, disease)
    if not concept:
        return _not_found("disease", disease)
    p = {"ui": concept["ui"], "k": limit}
    sub = "MATCH (:MeshDescriptor {ui: $ui})<-[:MESH_PARENT*0..10]-(m:MeshDescriptor) WITH DISTINCT m "
    drugs = client.read(
        sub + "MATCH (d:Drug)-[:INDICATED_FOR]->(i:IndicationTerm)-[:INDICATION_MAPS_TO]->(m) "
        "RETURN d.name AS drug, collect(DISTINCT m.name) AS via_concepts ORDER BY drug LIMIT $k", **p)
    reactions = client.read(
        sub + "MATCH (d:Drug)-[:INDICATED_FOR]->(:IndicationTerm)-[:INDICATION_MAPS_TO]->(m) "
        "WITH DISTINCT d MATCH (d)-[:SUSPECT_IN]->(e:AdverseEventReport)-[:HAS_REACTION]->(r:Reaction) "
        "RETURN r.name AS reaction, count(DISTINCT e) AS reports, collect(DISTINCT d.name)[..5] AS drugs "
        "ORDER BY reports DESC, reaction LIMIT $k", **p)
    trials = client.read(
        sub + "MATCH (t:ClinicalTrial)-[:STUDIES_CONDITION]->(:Condition)-[:CONDITION_MAPS_TO]->(m) "
        "RETURN count(DISTINCT t) AS n", **p)[0]["n"]
    icd = client.read(sub + "MATCH (m)-[:HAS_ICD10]->(c:ICD10Code) "
                      "RETURN DISTINCT c.code AS code, c.name AS name, m.name AS concept ORDER BY code LIMIT $k", **p)
    broader = client.read("MATCH (:MeshDescriptor {ui: $ui})-[:MESH_PARENT]->(b) RETURN b.name AS name", **p)
    return {"found": True, "concept": concept, "broader": [b["name"] for b in broader],
            "drugs": drugs, "top_reported_reactions": reactions, "clinical_trials": trials,
            "icd10": icd, "note": SAFETY_NOTE}


def drug_profile(client: Neo4jClient, name: str) -> dict:
    """The full 'what does MedGraph know about X' walkthrough."""
    resolved = resolve_drug(client, name)
    if not resolved:
        return _not_found("drug", name)
    d = resolved["drug"]
    info = client.read(
        "MATCH (d:Drug {name: $d}) "
        "RETURN [(a:DrugAlias)-[:ALIAS_OF]->(d) | a.display] AS aliases, "
        "       [(d)-[:HAS_PHARM_CLASS]->(p) | p.display] AS pharm_classes, "
        "       [(d)-[:HAS_LABEL]->(l) | {label_id: l.label_id, title: l.title, source: l.source}] AS labels, "
        "       [(d)-[:INDICATED_FOR]->(i) | {term: i.name, "
        "          mesh: [(i)-[:INDICATION_MAPS_TO]->(m) | m.name]}] AS indications",
        d=d,
    )[0]
    return {
        "found": True, "drug": resolved, **info,
        "atc": decode_atc(client, d),
        "safety": drug_safety(client, d, limit=10),
        "similar": similar_drugs(client, d, limit=10),
        "trials": trials_pipeline(client, drug=d, limit=10),
    }


# --------------------------------------------------------------------------- GraphRAG
DEFAULT_SEMANTIC_LABELS = ["MeshDescriptor", "Drug", "DrugAlias", "DrugLabel", "ClinicalTrial", "Reaction"]


def semantic_search(client: Neo4jClient, text: str, labels: list[str] | None = None, k: int = 5,
                    embedder=None) -> dict:
    """Vector search to find the closest concepts, then graph traversal for context."""
    from .embeddings import get_embedder

    onto = get_ontology()
    labels = [l for l in (labels or DEFAULT_SEMANTIC_LABELS)
              if l in onto.classes and onto.classes[l].embed_text]
    vec = (embedder or get_embedder()).embed_query(text)
    hits = []
    for label in labels:
        key = onto.classes[label].key
        hits += client.read(
            "CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score "
            f"RETURN $label AS label, node.{key} AS key, "
            "coalesce(node.display, node.name, node.title) AS text, round(score, 4) AS score",
            index=vector_index_name(label), k=k, vec=vec, label=label,
        )
    hits.sort(key=lambda h: h["score"], reverse=True)
    hits = hits[: k * 2]
    context = None
    top_concept = next((h for h in hits if h["label"] == "MeshDescriptor"), None)
    if top_concept:
        context = disease_overview(client, top_concept["text"], limit=5)
    return {"found": bool(hits), "query": text, "hits": hits, "graph_context": context}


# --------------------------------------------------------------------------- stats
def stats(client: Neo4jClient) -> dict:
    onto = get_ontology()
    nodes = {l: client.read(f"MATCH (n:{l}) RETURN count(n) AS n")[0]["n"] for l in onto.classes}
    rels = {r: client.read(f"MATCH ()-[x:{r}]->() RETURN count(x) AS n")[0]["n"] for r in onto.relationships}
    vector = client.read("SHOW VECTOR INDEXES YIELD name RETURN count(*) AS n")[0]["n"]
    embedded = client.read("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS n")[0]["n"]
    return {"nodes": sum(nodes.values()), "relationships": sum(rels.values()),
            "vector_indexes": vector, "embeddings": embedded,
            "nodes_by_label": nodes, "relationships_by_type": rels}
