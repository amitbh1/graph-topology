"""Open Energy Ontology (OEO) -> multi-parent Concept taxonomy with synonyms.

Licence: CC0 1.0. https://openenergyplatform.org/ontology/
"""

from __future__ import annotations

from typing import Iterator

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from ...config import settings
from ...etl.common import download
from ..base import SourceConnector, SourceMeta
from ..records import ConceptRecord

DEFINITION = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
EXACT_SYNONYM = URIRef("http://www.geneontology.org/formats/oboInOwl#hasExactSynonym")


def concept_code(uri: str) -> str | None:
    local = uri.rstrip("/").rsplit("/", 1)[-1].split("#")[-1]
    if local.startswith("OEO_"):
        return f"oeo:{local}"
    if local.startswith(("BFO_", "IAO_", "RO_")):
        return f"obo:{local}"
    return None


def parse_oeo(path) -> Iterator[dict]:
    g = Graph()
    g.parse(str(path))
    for cls in g.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue
        code, label = concept_code(str(cls)), g.value(cls, RDFS.label)
        if not code or not label:
            continue
        parents = sorted({p for p in (concept_code(str(o)) for o in g.objects(cls, RDFS.subClassOf)
                                      if isinstance(o, URIRef)) if p and p != code})
        synonyms = sorted({str(s) for s in g.objects(cls, EXACT_SYNONYM)} | {str(s) for s in g.objects(cls, SKOS.altLabel)})
        definition = g.value(cls, DEFINITION)
        yield {"code": code, "label": str(label)[:300], "parents": parents[:50],
               "synonyms": [s[:300] for s in synonyms][:100],
               "definition": str(definition)[:2000] if definition else None}


class OpenEnergyOntology(SourceConnector):
    meta = SourceMeta(
        source_id="oeo", title="Open Energy Ontology", kind="public", license="CC0 1.0",
        attribution="Open Energy Ontology, Open Energy Platform", url="https://openenergyplatform.org/ontology/",
        refresh="per release", record_kinds=("concept",),
    )

    def fetch(self, limit: int | None = None) -> Iterator[ConceptRecord]:
        path = download(settings.oeo_url, "oeo-full.owl")
        for i, row in enumerate(parse_oeo(path)):
            if limit and i >= limit:
                break
            yield ConceptRecord(source=self.meta.source_id, **row)
