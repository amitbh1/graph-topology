"""Load the Turtle ontology and use it as the schema contract.

The ontology (ontology/medical_graph.ttl) declares every node label, every
relationship type, their domains/ranges and the unique key of each label.
ETL, API, assistant and tests all read the schema from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS

from .config import settings

MG = Namespace("https://medgraph.local/ontology#")


@dataclass(frozen=True)
class NodeClass:
    name: str
    key: str | None
    embed_text: tuple[str, ...]
    comment: str = ""


@dataclass(frozen=True)
class RelType:
    name: str
    domains: tuple[str, ...]
    ranges: tuple[str, ...]
    comment: str = ""

    @property
    def domain(self) -> str | None:
        return self.domains[0] if len(self.domains) == 1 else None

    @property
    def range(self) -> str | None:
        return self.ranges[0] if len(self.ranges) == 1 else None


@dataclass
class Ontology:
    classes: dict[str, NodeClass] = field(default_factory=dict)
    relationships: dict[str, RelType] = field(default_factory=dict)

    # ------------------------------------------------------------------ checks
    def validate(self) -> list[str]:
        """Structural problems inside the ontology itself."""
        errors: list[str] = []
        for c in self.classes.values():
            if not c.key:
                errors.append(f"class {c.name} has no mg:keyProperty")
        for r in self.relationships.values():
            if len(r.domains) != 1:
                errors.append(f"relationship {r.name} must have exactly one rdfs:domain, has {len(r.domains)}")
            if len(r.ranges) != 1:
                errors.append(f"relationship {r.name} must have exactly one rdfs:range, has {len(r.ranges)}")
            for cls in (*r.domains, *r.ranges):
                if cls not in self.classes:
                    errors.append(f"relationship {r.name} references undeclared class {cls}")
        return errors

    def check_cypher(self, cypher: str) -> list[str]:
        """Labels / relationship types used in a Cypher string that are not declared."""
        labels, rels = extract_cypher_identifiers(cypher)
        errors = [f"undeclared node label :{l}" for l in sorted(labels - self.classes.keys())]
        errors += [f"undeclared relationship type :{r}" for r in sorted(rels - self.relationships.keys())]
        return errors

    # --------------------------------------------------------------- helpers
    @property
    def embedded_classes(self) -> list[NodeClass]:
        return [c for c in self.classes.values() if c.embed_text]

    def constraint_statements(self) -> list[str]:
        return [
            f"CREATE CONSTRAINT {c.name.lower()}_key IF NOT EXISTS "
            f"FOR (n:{c.name}) REQUIRE n.{c.key} IS UNIQUE"
            for c in self.classes.values()
        ]

    def schema_summary(self) -> str:
        """Compact schema text for LLM prompts (graph_qa)."""
        lines = ["Node labels (key property):"]
        lines += [f"  :{c.name} ({c.key}) — {c.comment}" for c in self.classes.values()]
        lines.append("Relationships:")
        lines += [
            f"  (:{r.domain})-[:{r.name}]->(:{r.range})" + (f" — {r.comment}" if r.comment else "")
            for r in self.relationships.values()
        ]
        return "\n".join(lines)


_LABEL_RE = re.compile(r"\(\w*:([A-Z][A-Za-z0-9_]*)")
_REL_RE = re.compile(r"-\[\w*:([A-Z][A-Z0-9_]*(?:\|[A-Z][A-Z0-9_]*)*)")


def extract_cypher_identifiers(text: str) -> tuple[set[str], set[str]]:
    """Find node-label patterns such as (d:Drug and relationship patterns such as
    -[r:SIMILAR_TO or -[:SUSPECT_IN|HAS_REACTION (Cypher relationships always follow '-')."""
    labels = set(_LABEL_RE.findall(text))
    rels: set[str] = set()
    for group in _REL_RE.findall(text):
        rels.update(group.split("|"))
    return labels, rels


def _local(uri) -> str:
    return str(uri).split("#")[-1]


def load_ontology(path: str | Path | None = None) -> Ontology:
    g = Graph()
    g.parse(str(path or settings.ontology_path), format="turtle")
    onto = Ontology()

    for cls in g.subjects(RDF.type, OWL.Class):
        name = str(g.value(cls, RDFS.label) or _local(cls))
        key = g.value(cls, MG.keyProperty)
        embed = g.value(cls, MG.embedText)
        onto.classes[name] = NodeClass(
            name=name,
            key=str(key) if key else None,
            embed_text=tuple(p.strip() for p in str(embed).split(",")) if embed else (),
            comment=str(g.value(cls, RDFS.comment) or ""),
        )

    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        name = _local(prop)
        onto.relationships[name] = RelType(
            name=name,
            domains=tuple(sorted(_local(d) for d in g.objects(prop, RDFS.domain))),
            ranges=tuple(sorted(_local(r) for r in g.objects(prop, RDFS.range))),
            comment=str(g.value(prop, RDFS.comment) or ""),
        )

    onto.classes = dict(sorted(onto.classes.items()))
    onto.relationships = dict(sorted(onto.relationships.items()))
    return onto


@lru_cache(maxsize=1)
def get_ontology() -> Ontology:
    return load_ontology()
