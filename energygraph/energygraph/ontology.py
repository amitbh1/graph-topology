"""Load the Turtle ontology and use it as the schema contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS

from .config import settings

EG = Namespace("https://energygraph.local/ontology#")
REQUIRED_NODE_PROPERTIES = ("source", "entitlement")


@dataclass(frozen=True)
class NodeClass:
    name: str
    key: str | None
    embed_text: tuple[str, ...]
    parent: str | None = None
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

    def validate(self) -> list[str]:
        errors: list[str] = []
        for c in self.classes.values():
            if not c.key:
                errors.append(f"class {c.name} has no eg:keyProperty")
            if c.parent and c.parent not in self.classes:
                errors.append(f"class {c.name} has undeclared parent {c.parent}")
        for r in self.relationships.values():
            if len(r.domains) != 1 or len(r.ranges) != 1:
                errors.append(f"relationship {r.name} must have exactly one domain and one range")
            for cls in (*r.domains, *r.ranges):
                if cls not in self.classes:
                    errors.append(f"relationship {r.name} references undeclared class {cls}")
        return errors

    def check_cypher(self, cypher: str) -> list[str]:
        labels, rels = extract_cypher_identifiers(cypher)
        errors = [f"undeclared node label :{l}" for l in sorted(labels - self.classes.keys())]
        errors += [f"undeclared relationship type :{r}" for r in sorted(rels - self.relationships.keys())]
        return errors

    @property
    def root_classes(self) -> list[NodeClass]:
        """Classes that own their key constraint (subclasses share the parent's)."""
        return [c for c in self.classes.values() if not c.parent]

    @property
    def asset_types(self) -> list[str]:
        return [c.name for c in self.classes.values() if c.parent == "Asset"]

    @property
    def embedded_classes(self) -> list[NodeClass]:
        return [c for c in self.classes.values() if c.embed_text]

    def constraint_statements(self) -> list[str]:
        return [f"CREATE CONSTRAINT {c.name.lower()}_key IF NOT EXISTS "
                f"FOR (n:{c.name}) REQUIRE n.{c.key} IS UNIQUE" for c in self.root_classes]

    def schema_summary(self) -> str:
        lines = ["Node labels (key property):"]
        for c in self.classes.values():
            extra = f" [also :{c.parent}]" if c.parent else ""
            lines.append(f"  :{c.name} ({c.key}){extra} — {c.comment}".rstrip(" —"))
        lines.append("Relationships:")
        lines += [f"  (:{r.domain})-[:{r.name}]->(:{r.range})" for r in self.relationships.values()]
        lines.append("Every node and relationship has `entitlement` and `source` properties.")
        return "\n".join(lines)


_LABEL_RE = re.compile(r"\(\w*:([A-Z][A-Za-z0-9_]*)")
_REL_RE = re.compile(r"-\[\w*:([A-Z][A-Z0-9_]*(?:\|[A-Z][A-Z0-9_]*)*)")


def extract_cypher_identifiers(text: str) -> tuple[set[str], set[str]]:
    """Node-label patterns like (a:Asset and relationship patterns like -[:OWNS|OPERATES."""
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
        key, embed, parent = g.value(cls, EG.keyProperty), g.value(cls, EG.embedText), g.value(cls, RDFS.subClassOf)
        onto.classes[name] = NodeClass(
            name=name, key=str(key) if key else None,
            embed_text=tuple(p.strip() for p in str(embed).split(",")) if embed else (),
            parent=_local(parent) if parent else None,
            comment=str(g.value(cls, RDFS.comment) or ""),
        )
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        onto.relationships[_local(prop)] = RelType(
            name=_local(prop),
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
