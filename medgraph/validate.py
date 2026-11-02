"""Ontology contract checks: ontology itself, Cypher in source code, and the live database."""

from __future__ import annotations

from pathlib import Path

from .ontology import Ontology, extract_cypher_identifiers, get_ontology

PACKAGE = Path(__file__).resolve().parent


def check_source(onto: Ontology, root: Path = PACKAGE) -> list[str]:
    """Every label / relationship type written in Cypher in the code base must be declared."""
    errors = []
    for path in sorted(root.rglob("*.py")):
        labels, rels = extract_cypher_identifiers(path.read_text(encoding="utf-8"))
        rel_path = path.relative_to(root.parent)
        errors += [f"{rel_path}: undeclared node label :{l}" for l in sorted(labels - onto.classes.keys())]
        errors += [f"{rel_path}: undeclared relationship type :{r}"
                   for r in sorted(rels - onto.relationships.keys())]
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
    for c in onto.classes.values():
        if constraints.get(c.name) != c.key:
            errors.append(f"database: :{c.name} key constraint is {constraints.get(c.name)!r}, "
                          f"ontology declares {c.key!r}")

    # Relationship endpoints in the data must respect the declared domain / range.
    for r in onto.relationships.values():
        bad = client.read(
            f"MATCH (a)-[x:{r.name}]->(b) WHERE NOT a:{r.domain} OR NOT b:{r.range} RETURN count(x) AS n"
        )[0]["n"]
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
