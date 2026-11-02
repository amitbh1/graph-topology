"""Contract checks: ontology, Cypher in source code, and the live database."""

from __future__ import annotations

from pathlib import Path

from .ontology import REQUIRED_NODE_PROPERTIES, Ontology, extract_cypher_identifiers, get_ontology

PACKAGE = Path(__file__).resolve().parent


def check_source(onto: Ontology, root: Path = PACKAGE) -> list[str]:
    errors = []
    for path in sorted(root.rglob("*.py")):
        labels, rels = extract_cypher_identifiers(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root.parent)
        errors += [f"{rel}: undeclared node label :{l}" for l in sorted(labels - onto.classes.keys())]
        errors += [f"{rel}: undeclared relationship type :{r}" for r in sorted(rels - onto.relationships.keys())]
    return errors


def check_database(onto: Ontology, client) -> list[str]:
    from .schema import database_constraints

    errors = []
    labels = {r["label"] for r in client.read("CALL db.labels() YIELD label RETURN label")}
    rels = {r["relationshipType"] for r in client.read(
        "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")}
    errors += [f"database: undeclared label :{l}" for l in sorted(labels - onto.classes.keys())]
    errors += [f"database: undeclared relationship type :{r}" for r in sorted(rels - onto.relationships.keys())]

    constraints = database_constraints(client)
    for c in onto.root_classes:
        if constraints.get(c.name) != c.key:
            errors.append(f"database: :{c.name} key constraint is {constraints.get(c.name)!r}, ontology {c.key!r}")

    for sub in onto.asset_types:
        n = client.read(f"MATCH (n:{sub}) WHERE NOT n:Asset RETURN count(n) AS n")[0]["n"]
        if n:
            errors.append(f"database: {n} :{sub} nodes lack the :Asset label")

    for prop in REQUIRED_NODE_PROPERTIES:
        n = client.read(f"MATCH (n) WHERE n.{prop} IS NULL RETURN count(n) AS n")[0]["n"]
        if n:
            errors.append(f"database: {n} nodes have no `{prop}`")
        n = client.read(f"MATCH ()-[r]->() WHERE r.{prop} IS NULL RETURN count(r) AS n")[0]["n"]
        if n:
            errors.append(f"database: {n} relationships have no `{prop}`")

    bad_ent = client.read("MATCH (n) WHERE n.entitlement IS NOT NULL AND NOT (n.entitlement = 'public' OR "
                          "n.entitlement STARTS WITH 'org:') RETURN count(n) AS n")[0]["n"]
    if bad_ent:
        errors.append(f"database: {bad_ent} nodes have an invalid entitlement value")

    from .config import settings

    if settings.is_production:
        seeded = client.read("MATCH (n) WHERE n.source = 'seed' RETURN count(n) AS n")[0]["n"]
        if seeded:
            errors.append(f"database: {seeded} synthetic seed nodes present in a production database")

    for r in onto.relationships.values():
        bad = client.read(f"MATCH (a)-[x:{r.name}]->(b) WHERE NOT a:{r.domain} OR NOT b:{r.range} "
                          "RETURN count(x) AS n")[0]["n"]
        if bad:
            errors.append(f"database: {bad} :{r.name} relationships violate ({r.domain})->({r.range})")
    return errors


def validate_all() -> int:
    from .db import get_client

    onto = get_ontology()
    print(f"ontology: {len(onto.classes)} classes, {len(onto.relationships)} relationship types")
    errors = onto.validate() + check_source(onto)
    client = get_client()
    if client.ping():
        errors += check_database(onto, client)
    else:
        print("database: not reachable — skipped")
    for e in errors:
        print("  ERROR", e)
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0
