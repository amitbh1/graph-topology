"""Deterministic, entitlement-filtered graph queries — shared by the API, dashboard and assistant.

Rules every function follows:
* takes `ents` (the caller's entitlements) and filters EVERY node with V()
  and EVERY relationship with R() — licensed facts never reach an unentitled caller;
* parameterised Cypher only (labels come from code, never from input);
* returns `found: False` + message instead of guessing;
* returns `sources` (ids + attribution) for everything it reports.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .db import Neo4jClient
from .etl.common import norm
from .ontology import get_ontology
from .schema import vector_index_name
from .sources.base import registry
from .timeseries.store import get_store

INCIDENT_NOTE = "Incidents are reported events from the listed sources, not root-cause findings."
SIMILARITY_NOTE = ("Exploratory peer score = 2*shared fuels/technologies + 2*capacity closeness + vintage match "
                   "+ shared owner. Not an investment or operational recommendation.")
PRICE_NOTE = ("Published observations only, as stored from the listed source. EnergyGraph does not estimate, "
              "interpolate or forecast prices.")


def V(x: str) -> str:
    """Visible node: entitled and not a stub."""
    return f"(('*' IN $ents OR {x}.entitlement IN $ents) AND NOT coalesce({x}.stub, false))"


def R(x: str) -> str:
    """Visible relationship."""
    return f"('*' IN $ents OR {x}.entitlement IN $ents)"


def _not_found(kind: str, value: str) -> dict:
    return {"found": False, "message": f"No {kind} matching '{value}' is visible in EnergyGraph for your entitlements."}


def _lucene_fuzzy(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    return " AND ".join(f"{w}~" if len(w) > 4 else w for w in words) or '""'


def _lucene_phrase(term: str) -> str:
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def attribution(source_ids) -> list[dict]:
    reg = registry()
    out = []
    for sid in sorted({s for s in source_ids if s}):
        meta = reg[sid].meta if sid in reg else None
        out.append({"source": sid, "attribution": meta.attribution if meta else sid,
                    "license": meta.license if meta else None})
    return out


def _sources(*rows_or_values) -> list[str]:
    found: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "source" and isinstance(x, str):
                    found.add(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for v in rows_or_values:
        walk(v)
    return sorted(found)


# --------------------------------------------------------------------------- resolution
def resolve_asset(db: Neo4jClient, name: str, ents: list[str]) -> dict | None:
    n = norm(name)
    rows = db.read(
        f"MATCH (a:Asset) WHERE (a.asset_id = $raw OR a.name_lower = $n) AND {V('a')} "
        "RETURN a.asset_id AS asset_id, a.name AS name, a.asset_type AS asset_type, a.country AS country, "
        "'exact' AS via LIMIT 5 "
        "UNION "
        f"MATCH (al:AssetAlias {{name: $n}})-[x:ASSET_ALIAS_OF]->(a:Asset) WHERE {V('al')} AND {R('x')} AND {V('a')} "
        "RETURN a.asset_id AS asset_id, a.name AS name, a.asset_type AS asset_type, a.country AS country, "
        "'alias' AS via LIMIT 5",
        raw=name.strip(), n=n, ents=ents)
    if not rows and n:
        rows = db.read(
            "CALL db.index.fulltext.queryNodes('entity_names', $q, {limit: 25}) YIELD node, score "
            "WHERE (node:Asset OR node:AssetAlias) "
            "OPTIONAL MATCH (node)-[x:ASSET_ALIAS_OF]->(t:Asset) "
            "WITH coalesce(t, node) AS a, node, x, score "
            f"WHERE a:Asset AND {V('a')} AND {V('node')} AND (x IS NULL OR {R('x')}) "
            "RETURN DISTINCT a.asset_id AS asset_id, a.name AS name, a.asset_type AS asset_type, "
            "a.country AS country, 'fuzzy' AS via, score ORDER BY score DESC LIMIT 5",
            q=_lucene_fuzzy(name), ents=ents)
    if not rows:
        return None
    best = rows[0]
    alternatives = [r for r in rows[1:] if r["asset_id"] != best["asset_id"]][:4]
    return {"input": name, **{k: best[k] for k in ("asset_id", "name", "asset_type", "country", "via")},
            "alternatives": [{k: r[k] for k in ("asset_id", "name", "asset_type", "country")} for r in alternatives]}


def resolve_company(db: Neo4jClient, name: str, ents: list[str]) -> dict | None:
    n = norm(name)
    rows = db.read(
        f"MATCH (c:Company) WHERE (c.company_id = $raw OR c.name_lower = $n OR c.lei = $up) AND {V('c')} "
        "RETURN c.company_id AS company_id, c.name AS name, c.lei AS lei, 'exact' AS via LIMIT 5 "
        "UNION "
        f"MATCH (al:CompanyAlias {{name: $n}})-[x:ALIAS_OF]->(c:Company) WHERE {V('al')} AND {R('x')} AND {V('c')} "
        "RETURN c.company_id AS company_id, c.name AS name, c.lei AS lei, 'alias' AS via LIMIT 5",
        raw=name.strip(), n=n, up=name.strip().upper(), ents=ents)
    if not rows and n:
        rows = db.read(
            "CALL db.index.fulltext.queryNodes('entity_names', $q, {limit: 25}) YIELD node, score "
            "WHERE (node:Company OR node:CompanyAlias) "
            "OPTIONAL MATCH (node)-[x:ALIAS_OF]->(t:Company) "
            "WITH coalesce(t, node) AS c, node, x, score "
            f"WHERE c:Company AND {V('c')} AND {V('node')} AND (x IS NULL OR {R('x')}) "
            "RETURN DISTINCT c.company_id AS company_id, c.name AS name, c.lei AS lei, 'fuzzy' AS via, score "
            "ORDER BY score DESC LIMIT 5", q=_lucene_fuzzy(name), ents=ents)
    if not rows:
        return None
    best = rows[0]
    return {"input": name, **{k: best[k] for k in ("company_id", "name", "lei", "via")},
            "alternatives": [{"company_id": r["company_id"], "name": r["name"]} for r in rows[1:4]
                             if r["company_id"] != best["company_id"]]}


def resolve_concept(db: Neo4jClient, term: str, ents: list[str]) -> dict | None:
    n = norm(term)
    rows = db.read(
        f"MATCH (c:Concept) WHERE (c.code = $raw OR c.label_lower = $n OR $n IN c.synonyms_lower) AND {V('c')} "
        "RETURN c.code AS code, c.label AS label, CASE WHEN c.label_lower = $n OR c.code = $raw THEN 0 ELSE 1 END AS rank "
        "ORDER BY rank LIMIT 1", raw=term.strip(), n=n, ents=ents)
    if not rows and n:
        rows = db.read(
            "CALL db.index.fulltext.queryNodes('entity_names', $q, {limit: 10}) YIELD node, score "
            f"WHERE node:Concept AND {V('node')} RETURN node.code AS code, node.label AS label "
            "ORDER BY score DESC LIMIT 1", q=_lucene_fuzzy(term), ents=ents)
    return {"input": term, "code": rows[0]["code"], "label": rows[0]["label"]} if rows else None


# --------------------------------------------------------------------------- assets & companies
def asset_overview(db: Neo4jClient, asset: str, ents: list[str]) -> dict:
    res = resolve_asset(db, asset, ents)
    if not res:
        return _not_found("asset", asset)
    p = {"id": res["asset_id"], "ents": ents}
    info = db.read(
        f"MATCH (a:Asset {{asset_id: $id}}) WHERE {V('a')} "
        "RETURN a {.asset_id, .name, .asset_type, .country, .latitude, .longitude, .capacity, .capacity_unit, "
        "          .status, .commissioning_year, .source, .as_of} AS asset, "
        f"  [(al:AssetAlias)-[x:ASSET_ALIAS_OF]->(a) WHERE {V('al')} AND {R('x')} | al.display] AS aliases, "
        f"  [(a)-[x:USES]->(c:Concept) WHERE {V('c')} AND {R('x')} | c.label] AS fuels_technologies, "
        f"  [(a)-[x:LOCATED_IN]->(g:Region) WHERE {V('g')} AND {R('x')} | g.name] AS location, "
        f"  [(o:Company)-[x:OWNS]->(a) WHERE {V('o')} AND {R('x')} | "
        "     {company_id: o.company_id, name: o.name, share: x.share, source: x.source, as_of: x.as_of}] AS owners, "
        f"  [(o:Company)-[x:OPERATES]->(a) WHERE {V('o')} AND {R('x')} | "
        "     {company_id: o.company_id, name: o.name, source: x.source}] AS operators", **p)
    if not info:
        return _not_found("asset", asset)
    info = info[0]
    incidents = db.read(
        f"MATCH (a:Asset {{asset_id: $id}})-[x:HAD_INCIDENT]->(i:Incident) WHERE {R('x')} AND {V('i')} "
        "RETURN i.incident_id AS incident_id, i.incident_type AS type, toString(i.start) AS start, "
        "toString(i.end) AS end, i.capacity_offline AS capacity_offline, i.capacity_unit AS unit, i.source AS source "
        "ORDER BY i.start DESC LIMIT 5", **p)
    projects = db.read(
        f"MATCH (pr:Project)-[x:EXPANDS]->(a:Asset {{asset_id: $id}}) WHERE {R('x')} AND {V('pr')} "
        "RETURN pr.project_id AS project_id, pr.name AS name, pr.status AS status, pr.capacity AS capacity, "
        "pr.capacity_unit AS unit, pr.expected_year AS expected_year, pr.source AS source", **p)
    network = db.read(
        f"MATCH (a:Asset {{asset_id: $id}}) "
        f"RETURN [(u:Asset)-[x:CONNECTS_TO]->(a) WHERE {V('u')} AND {R('x')} | "
        "         {asset_id: u.asset_id, name: u.name, asset_type: u.asset_type, source: x.source}] AS upstream, "
        f"       [(a)-[x:CONNECTS_TO]->(d:Asset) WHERE {V('d')} AND {R('x')} | "
        "         {asset_id: d.asset_id, name: d.name, asset_type: d.asset_type, source: x.source}] AS downstream", **p)[0]
    docs = db.read(
        f"MATCH (d:Document)-[x:MENTIONS_ASSET]->(:Asset {{asset_id: $id}}) WHERE {V('d')} AND {R('x')} "
        "RETURN d.doc_id AS doc_id, d.title AS title, toString(d.published_at) AS published_at, d.source AS source "
        "ORDER BY d.published_at DESC LIMIT 5", **p)
    n_similar = db.read(f"MATCH (:Asset {{asset_id: $id}})-[s:SIMILAR_TO]-(b:Asset) WHERE {R('s')} AND {V('b')} "
                        "RETURN count(DISTINCT b) AS n", **p)[0]["n"]
    out = {"found": True, "resolved": res, **info, "recent_incidents": incidents, "projects": projects,
           "network": network, "documents": docs, "similar_assets_count": n_similar, "incident_note": INCIDENT_NOTE}
    out["sources"] = attribution(_sources(out))
    return out


def ownership_chain(db: Neo4jClient, entity: str, ents: list[str], max_depth: int = 6) -> dict:
    depth = max(1, min(int(max_depth), 10))
    company = resolve_company(db, entity, ents)
    if company:
        p = {"id": company["company_id"], "ents": ents}
        parents = db.read(
            f"MATCH path = (c:Company {{company_id: $id}})-[:SUBSIDIARY_OF*1..{depth}]->(top:Company) "
            f"WHERE all(n IN nodes(path) WHERE {V('n')}) AND all(r IN relationships(path) WHERE {R('r')}) "
            "RETURN [n IN nodes(path)[1..] | {company_id: n.company_id, name: n.name, lei: n.lei}] AS chain, "
            "[r IN relationships(path) | r.source] AS sources ORDER BY length(path) DESC LIMIT 1", **p)
        subsidiaries = db.read(
            f"MATCH (s:Company)-[x:SUBSIDIARY_OF]->(c:Company {{company_id: $id}}) WHERE {V('s')} AND {R('x')} "
            "RETURN s.company_id AS company_id, s.name AS name, x.source AS source ORDER BY name LIMIT 50", **p)
        assets = db.read(
            f"MATCH (c:Company {{company_id: $id}}) "
            f"MATCH path = (s:Company)-[:SUBSIDIARY_OF*0..{depth}]->(c) "
            f"WHERE all(n IN nodes(path) WHERE {V('n')}) AND all(r IN relationships(path) WHERE {R('r')}) "
            f"MATCH (s)-[x:OWNS|OPERATES]->(a:Asset) WHERE {R('x')} AND {V('a')} "
            "WITH a, collect(DISTINCT {via: s.name, role: type(x), share: x.share, source: x.source}) AS links "
            "RETURN a.asset_id AS asset_id, a.name AS name, a.asset_type AS asset_type, a.country AS country, "
            "a.capacity AS capacity, a.capacity_unit AS unit, links ORDER BY a.asset_type, a.name LIMIT 100", **p)
        out = {"found": True, "company": company,
               "parent_chain": parents[0]["chain"] if parents else [],
               "ultimate_parent": parents[0]["chain"][-1] if parents else None,
               "subsidiaries": subsidiaries, "assets": assets, "asset_count_shown": len(assets)}
        out["sources"] = attribution(_sources(out) + (parents[0]["sources"] if parents else []))
        return out
    asset = resolve_asset(db, entity, ents)
    if not asset:
        return _not_found("company or asset", entity)
    owners = db.read(
        f"MATCH (o:Company)-[x:OWNS|OPERATES]->(a:Asset {{asset_id: $id}}) WHERE {V('o')} AND {R('x')} "
        "RETURN DISTINCT o.company_id AS company_id, o.name AS name, type(x) AS role, x.share AS share, "
        "x.source AS source", id=asset["asset_id"], ents=ents)
    chains = []
    for o in owners:
        sub = ownership_chain(db, o["company_id"], ents, depth)
        chains.append({"owner": o, "parent_chain": sub.get("parent_chain", [])})
    out = {"found": True, "asset": asset, "owners": chains}
    out["sources"] = attribution(_sources(out))
    return out


def similar_assets(db: Neo4jClient, asset: str, ents: list[str], limit: int = 10) -> dict:
    res = resolve_asset(db, asset, ents)
    if not res:
        return _not_found("asset", asset)
    rows = db.read(
        f"MATCH (a:Asset {{asset_id: $id}})-[s:SIMILAR_TO]-(b:Asset) WHERE {R('s')} AND {V('b')} "
        "WITH b, max(s.score) AS score, collect(s)[0] AS s "
        "RETURN b.asset_id AS asset_id, b.name AS name, b.country AS country, b.capacity AS capacity, "
        "b.capacity_unit AS unit, b.commissioning_year AS commissioning_year, score, "
        "s.shared_concepts AS shared_fuels_technologies, s.capacity_closeness AS capacity_closeness, "
        "s.vintage_match AS vintage_match, s.shared_owner AS shared_owner, b.source AS source "
        "ORDER BY score DESC, name LIMIT $k", id=res["asset_id"], ents=ents, k=limit)
    out = {"found": True, "asset": res, "similar": rows, "note": SIMILARITY_NOTE}
    out["sources"] = attribution(_sources(rows))
    return out


# --------------------------------------------------------------------------- taxonomy
def taxonomy_expand(db: Neo4jClient, term: str, ents: list[str], max_depth: int = 5, max_terms: int = 200) -> dict:
    concept = resolve_concept(db, term, ents)
    if not concept:
        return _not_found("concept", term)
    depth = max(0, min(int(max_depth), 10))
    p = {"code": concept["code"], "ents": ents}
    narrower = db.read(
        f"MATCH path = (root:Concept {{code: $code}})<-[:CONCEPT_PARENT*0..{depth}]-(c:Concept) "
        f"WHERE all(n IN nodes(path) WHERE {V('n')}) AND all(r IN relationships(path) WHERE {R('r')}) "
        "WITH DISTINCT c RETURN c.code AS code, c.label AS label, c.synonyms AS synonyms, c.source AS source", **p)
    broader = db.read(
        f"MATCH (:Concept {{code: $code}})-[x:CONCEPT_PARENT]->(b:Concept) WHERE {R('x')} AND {V('b')} "
        "RETURN b.code AS code, b.label AS label", **p)
    codes = [r["code"] for r in narrower]
    usage = db.read(
        f"MATCH (a:Asset)-[x:USES]->(c:Concept) WHERE c.code IN $codes AND {R('x')} AND {V('a')} "
        "RETURN a.asset_type AS asset_type, count(DISTINCT a) AS assets ORDER BY assets DESC",
        codes=codes, ents=ents)
    terms = sorted({norm(r["label"]) for r in narrower} | {norm(s) for r in narrower for s in (r["synonyms"] or [])})
    definition = db.read(f"MATCH (c:Concept {{code: $code}}) WHERE {V('c')} RETURN c.definition AS d", **p)
    out = {"found": True, "concept": concept, "definition": definition[0]["d"] if definition else None,
           "broader": broader, "narrower": [r["label"] for r in narrower if r["code"] != concept["code"]][:100],
           "term_count": len(terms), "terms": terms[:max_terms], "assets_using": usage}
    out["sources"] = attribution(_sources(narrower))
    return out


def decode_classification(db: Neo4jClient, term_or_code: str, ents: list[str]) -> dict:
    concept = resolve_concept(db, term_or_code, ents)
    if not concept:
        return _not_found("concept", term_or_code)
    paths = db.read(
        f"MATCH path = (c:Concept {{code: $code}})-[:CONCEPT_PARENT*0..12]->(top:Concept) "
        f"WHERE NOT EXISTS {{ (top)-[:CONCEPT_PARENT]->() }} "
        f"AND all(n IN nodes(path) WHERE {V('n')}) AND all(r IN relationships(path) WHERE {R('r')}) "
        "RETURN [n IN reverse(nodes(path)) | {code: n.code, label: n.label}] AS path LIMIT 10",
        code=concept["code"], ents=ents)
    return {"found": True, "concept": concept, "paths": [p["path"] for p in paths],
            "sources": attribution(["oeo"] if concept["code"].startswith("oeo:") else [])}


# --------------------------------------------------------------------------- documents
def search_documents(db: Neo4jClient, term: str, ents: list[str], expand: bool = True, limit: int = 10) -> dict:
    base_terms = [norm(term)]
    exp = taxonomy_expand(db, term, ents, max_terms=300) if expand else {"found": False}
    terms = exp["terms"] if exp.get("found") else base_terms

    def run(ts):
        q = " OR ".join(_lucene_phrase(t) for t in ts[:500])
        return db.read(
            "CALL db.index.fulltext.queryNodes('document_text', $q, {limit: 500}) YIELD node, score "
            f"WHERE {V('node')} "
            "RETURN node.doc_id AS doc_id, node.title AS title, toString(node.published_at) AS published_at, "
            "node.source AS source, substring(node.text, 0, 300) AS snippet, score ORDER BY score DESC",
            q=q, ents=ents)

    baseline, expanded = run(base_terms), run(terms)
    gain = len(expanded) - len(baseline)
    out = {"found": bool(expanded), "query": term, "expanded": bool(exp.get("found")),
           "expanded_term_count": len(terms), "baseline_matches": len(baseline), "expanded_matches": len(expanded),
           "recall_gain_pct": round(100 * gain / len(baseline), 1) if baseline else None,
           "documents": expanded[:limit]}
    out["sources"] = attribution(_sources(expanded[:limit]))
    return out


# --------------------------------------------------------------------------- incidents & projects
def incident_history(db: Neo4jClient, entity: str, ents: list[str], start: dt.date | None = None,
                     end: dt.date | None = None, incident_type: str | None = None, limit: int = 25) -> dict:
    asset = resolve_asset(db, entity, ents)
    company = None if asset else resolve_company(db, entity, ents)
    if not asset and not company:
        return _not_found("asset or company", entity)
    if asset:
        match = f"MATCH (a:Asset {{asset_id: $id}}) WHERE {V('a')} "
        pid = asset["asset_id"]
    else:
        match = (f"MATCH (c:Company {{company_id: $id}})-[o:OWNS|OPERATES]->(a:Asset) "
                 f"WHERE {V('c')} AND {R('o')} AND {V('a')} WITH DISTINCT a ")
        pid = company["company_id"]
    q = (match + f"MATCH (a)-[x:HAD_INCIDENT]->(i:Incident) WHERE {R('x')} AND {V('i')} "
         "AND ($start IS NULL OR i.start >= date($start)) AND ($end IS NULL OR i.start <= date($end)) "
         "AND ($type IS NULL OR i.incident_type = $type) ")
    params = {"id": pid, "ents": ents, "start": start.isoformat() if start else None,
              "end": end.isoformat() if end else None, "type": incident_type}
    rows = db.read(q + "RETURN i.incident_id AS incident_id, a.name AS asset, a.asset_id AS asset_id, "
                   "i.incident_type AS type, toString(i.start) AS start, toString(i.end) AS end, "
                   "CASE WHEN i.end IS NULL THEN null ELSE duration.inDays(i.start, i.end).days END AS duration_days, "
                   "i.capacity_offline AS capacity_offline, i.capacity_unit AS unit, i.description AS description, "
                   "i.source AS source ORDER BY i.start DESC LIMIT $k", **params, k=limit)
    by_type = db.read(q + "RETURN i.incident_type AS type, count(*) AS incidents ORDER BY incidents DESC", **params)
    out = {"found": bool(rows), "subject": asset or company, "total": sum(r["incidents"] for r in by_type),
           "by_type": by_type, "incidents": rows, "note": INCIDENT_NOTE}
    if not rows:
        out["message"] = "No incidents visible for this subject and period."
    out["sources"] = attribution(_sources(rows))
    return out


def project_pipeline(db: Neo4jClient, ents: list[str], country: str | None = None, asset_type: str | None = None,
                     concept: str | None = None, company: str | None = None, status: str | None = None,
                     limit: int = 25) -> dict:
    params: dict[str, Any] = {"ents": ents, "country": country, "atype": asset_type, "status": status,
                              "codes": None, "cid": None}
    subject: dict[str, Any] = {k: v for k, v in (("country", country), ("asset_type", asset_type),
                                                 ("status", status)) if v}
    if concept:
        exp = taxonomy_expand(db, concept, ents, max_terms=0)
        if not exp.get("found"):
            return _not_found("concept", concept)
        params["codes"] = db.read(
            f"MATCH (:Concept {{code: $code}})<-[:CONCEPT_PARENT*0..5]-(c:Concept) WHERE {V('c')} "
            "RETURN collect(DISTINCT c.code) AS codes", code=exp["concept"]["code"], ents=ents)[0]["codes"]
        subject["concept"] = exp["concept"]
    if company:
        res = resolve_company(db, company, ents)
        if not res:
            return _not_found("company", company)
        params["cid"] = res["company_id"]
        subject["company"] = res
    base = (f"MATCH (p:Project) WHERE {V('p')} "
            "AND ($country IS NULL OR p.country = $country) AND ($atype IS NULL OR p.asset_type = $atype) "
            "AND ($status IS NULL OR p.status = $status) "
            f"AND ($codes IS NULL OR EXISTS {{ (p)-[x:PROJECT_USES]->(c:Concept) WHERE c.code IN $codes AND {R('x')} }}) "
            f"AND ($cid IS NULL OR EXISTS {{ (:Company {{company_id: $cid}})-[x:DEVELOPS]->(p) WHERE {R('x')} }}) ")
    rows = db.read(
        base + f"RETURN p.project_id AS project_id, p.name AS name, p.asset_type AS asset_type, p.status AS status, "
        "p.capacity AS capacity, p.capacity_unit AS unit, p.country AS country, p.expected_year AS expected_year, "
        f"[(c:Company)-[x:DEVELOPS]->(p) WHERE {V('c')} AND {R('x')} | c.name] AS developers, "
        f"[(p)-[x:EXPANDS]->(a:Asset) WHERE {V('a')} AND {R('x')} | a.name] AS expands, p.source AS source "
        "ORDER BY p.expected_year, p.name LIMIT $k", **params, k=limit)
    by_status = db.read(base + "RETURN p.status AS status, count(*) AS projects, "
                        "p.capacity_unit AS unit, sum(p.capacity) AS total_capacity ORDER BY projects DESC", **params)
    out = {"found": bool(rows), "filters": subject, "total_projects": sum(r["projects"] for r in by_status),
           "by_status": by_status, "projects": rows}
    if not rows:
        out["message"] = "No projects visible for these filters."
    out["sources"] = attribution(_sources(rows))
    return out


# --------------------------------------------------------------------------- network
def supply_path(db: Neo4jClient, origin: str, destination: str | None, ents: list[str], max_hops: int = 6) -> dict:
    a = resolve_asset(db, origin, ents)
    if not a:
        return _not_found("asset", origin)
    hops = max(1, min(int(max_hops), 10))
    if destination:
        b = resolve_asset(db, destination, ents)
        if not b:
            return _not_found("asset", destination)
        rows = db.read(
            f"MATCH (a:Asset {{asset_id: $a}}), (b:Asset {{asset_id: $b}}) "
            f"MATCH path = shortestPath((a)-[:CONNECTS_TO*1..{hops}]->(b)) "
            f"WHERE all(n IN nodes(path) WHERE {V('n')}) AND all(r IN relationships(path) WHERE {R('r')}) "
            "RETURN [n IN nodes(path) | {asset_id: n.asset_id, name: n.name, asset_type: n.asset_type}] AS nodes, "
            "[r IN relationships(path) | {capacity: r.capacity, unit: r.capacity_unit, source: r.source}] AS links",
            a=a["asset_id"], b=b["asset_id"], ents=ents)
        if not rows:
            return {"found": False, "from": a, "to": b,
                    "message": f"No visible physical path within {hops} connections."}
        out = {"found": True, "from": a, "to": b, "path": rows[0]["nodes"], "links": rows[0]["links"],
               "bottleneck": min((l["capacity"] for l in rows[0]["links"] if l["capacity"] is not None), default=None)}
        out["sources"] = attribution(_sources(rows[0]["links"]))
        return out
    rows = db.read(
        f"MATCH (a:Asset {{asset_id: $a}}) "
        f"OPTIONAL MATCH up = (u:Asset)-[:CONNECTS_TO*1..{hops}]->(a) "
        f"WHERE all(n IN nodes(up) WHERE {V('n')}) AND all(r IN relationships(up) WHERE {R('r')}) "
        "WITH a, collect(DISTINCT {asset_id: u.asset_id, name: u.name, asset_type: u.asset_type, hops: length(up)}) AS ups "
        f"OPTIONAL MATCH down = (a)-[:CONNECTS_TO*1..{hops}]->(d:Asset) "
        f"WHERE all(n IN nodes(down) WHERE {V('n')}) AND all(r IN relationships(down) WHERE {R('r')}) "
        "RETURN ups, collect(DISTINCT {asset_id: d.asset_id, name: d.name, asset_type: d.asset_type, "
        "hops: length(down)}) AS downs", a=a["asset_id"], ents=ents)[0]
    ups = [u for u in rows["ups"] if u["asset_id"]]
    downs = [d for d in rows["downs"] if d["asset_id"]]
    return {"found": bool(ups or downs), "asset": a, "upstream": ups[:50], "downstream": downs[:50],
            **({} if ups or downs else {"message": "No visible network connections for this asset."})}


# --------------------------------------------------------------------------- prices
def price_series(db: Neo4jClient, query: str, ents: list[str], start: dt.date | None = None,
                 end: dt.date | None = None, max_points: int = 60) -> dict:
    n = norm(query)
    rows = db.read(
        f"MATCH (s:PriceSeries) WHERE {V('s')} AND (s.series_id = $raw OR toLower(s.name) CONTAINS $n "
        f"  OR toLower(s.commodity) = $n "
        f"  OR EXISTS {{ (s)-[x:PRICED_AT]->(h:Hub) WHERE {R('x')} AND (toLower(h.name) CONTAINS $n OR h.code = $raw) }}) "
        "OPTIONAL MATCH (s)-[x:PRICED_AT]->(h:Hub) "
        "RETURN s.series_id AS series_id, s.name AS name, s.unit AS unit, s.frequency AS frequency, "
        "s.methodology_url AS methodology_url, s.source AS source, s.entitlement AS entitlement, "
        "head(collect(h.name)) AS hub ORDER BY CASE WHEN s.series_id = $raw THEN 0 ELSE 1 END, s.name LIMIT 5",
        raw=query.strip(), n=n, ents=ents)
    if not rows:
        return _not_found("price series", query)
    series, others = rows[0], rows[1:]
    store = get_store()
    obs = store.observations(series["series_id"], start, end, limit=max(1, min(max_points, 400)))
    summary = store.summary(series["series_id"], start, end)
    out = {"found": bool(obs), "series": {k: v for k, v in series.items() if k != "entitlement"},
           "other_matches": [{"series_id": o["series_id"], "name": o["name"]} for o in others],
           "period": {"start": start.isoformat() if start else summary["first_date"],
                      "end": end.isoformat() if end else summary["last_date"]},
           "summary": summary, "observations": [{"date": d, "value": v} for d, v in obs], "note": PRICE_NOTE,
           "sources": attribution([series["source"]])}
    if not obs:
        out["message"] = "No published observations stored for this series and period."
    return out


# --------------------------------------------------------------------------- catalog & semantic
def list_sources(db: Neo4jClient, principal) -> dict:
    out = []
    for sid, conn in sorted(registry().items()):
        m = conn.meta
        entitled = all(principal.sees(e) for e in m.entitlements)
        ok, reason = conn.available() if entitled else (None, "not entitled")
        out.append({"source": sid, "title": m.title, "kind": m.kind, "license": m.license,
                    "attribution": m.attribution, "refresh": m.refresh, "entitled": entitled,
                    "available": ok, "status": reason if not ok else "ok"})
    return {"found": True, "sources": out}


DEFAULT_SEMANTIC_LABELS = ["Asset", "Company", "Concept", "Project", "Document"]


def semantic_search(db: Neo4jClient, text: str, ents: list[str], k: int = 5, embedder=None) -> dict:
    from .embeddings import get_embedder

    onto = get_ontology()
    vec = (embedder or get_embedder()).embed_query(text)
    hits = []
    for label in DEFAULT_SEMANTIC_LABELS:
        key = onto.classes[label].key
        hits += db.read(
            "CALL db.index.vector.queryNodes($index, $over, $vec) YIELD node, score "
            f"WHERE {V('node')} "
            f"RETURN $label AS label, node.{key} AS key, coalesce(node.name, node.label, node.title) AS text, "
            "node.source AS source, round(score, 4) AS score ORDER BY score DESC LIMIT $k",
            index=vector_index_name(label), over=k * 10, vec=vec, label=label, k=k, ents=ents)
    hits.sort(key=lambda h: h["score"], reverse=True)
    hits = hits[: k * 2]
    context = None
    top_asset = next((h for h in hits if h["label"] == "Asset"), None)
    if top_asset:
        context = asset_overview(db, top_asset["key"], ents)
    return {"found": bool(hits), "query": text, "hits": hits, "graph_context": context,
            "sources": attribution(_sources(hits))}


def stats(db: Neo4jClient) -> dict:
    onto = get_ontology()
    nodes = {l: db.read(f"MATCH (n:{l}) WHERE NOT coalesce(n.stub, false) RETURN count(n) AS n")[0]["n"]
             for l in onto.classes}
    rels = {r: db.read(f"MATCH ()-[x:{r}]->() RETURN count(x) AS n")[0]["n"] for r in onto.relationships}
    by_ent = db.read("MATCH (n) WHERE n.entitlement IS NOT NULL RETURN n.entitlement AS entitlement, count(*) AS n "
                     "ORDER BY n DESC")
    vector = db.read("SHOW VECTOR INDEXES YIELD name RETURN count(*) AS n")[0]["n"]
    embedded = db.read("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS n")[0]["n"]
    stubs = db.read("MATCH (n) WHERE n.stub = true RETURN count(n) AS n")[0]["n"]
    return {"nodes": sum(v for k, v in nodes.items() if not onto.classes[k].parent),
            "relationships": sum(rels.values()), "vector_indexes": vector, "embeddings": embedded,
            "unresolved_stubs": stubs, "nodes_by_label": nodes, "relationships_by_type": rels,
            "nodes_by_entitlement": {r["entitlement"]: r["n"] for r in by_ent}}
