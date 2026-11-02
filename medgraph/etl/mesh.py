"""MeSH descriptors (NLM XML) -> multi-parent descriptor hierarchy + entry terms.

A descriptor can have several tree numbers (e.g. Diabetes Mellitus is C18.452.394.750
and C19.246), so it can have several parents. The parent of tree number
`C18.452.394.750` is the descriptor holding `C18.452.394`.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from typing import IO, Iterator

from ..config import settings
from ..db import Neo4jClient
from .common import download, norm

DISEASE_TREE_PREFIXES = ("C", "F03")
GZIP_MAGIC = b"\x1f\x8b"


def parse_mesh_xml(fh: IO[bytes]) -> Iterator[dict]:
    for _, elem in ET.iterparse(fh, events=("end",)):
        if elem.tag != "DescriptorRecord":
            continue
        terms: set[str] = set()
        scope = ""
        for concept in elem.iterfind("ConceptList/Concept"):
            if concept.get("PreferredConceptYN") == "Y":
                scope = " ".join((concept.findtext("ScopeNote") or "").split())
            for s in concept.iterfind("TermList/Term/String"):
                terms.add(s.text or "")
        name = elem.findtext("DescriptorName/String") or ""
        trees = [t.text for t in elem.iterfind("TreeNumberList/TreeNumber") if t.text]
        yield {
            "ui": elem.findtext("DescriptorUI"),
            "name": name,
            "scope_note": scope[:1000],
            "tree_numbers": trees,
            "is_disease": any(t.startswith(DISEASE_TREE_PREFIXES) for t in trees),
            "terms": sorted({t for t in terms | {name} if t}),
        }
        elem.clear()


def hierarchy_pairs(records: list[dict]) -> set[tuple[str, str]]:
    """(child_ui, parent_ui) pairs derived from tree numbers."""
    tree_to_ui = {t: r["ui"] for r in records for t in r["tree_numbers"]}
    pairs = set()
    for r in records:
        for t in r["tree_numbers"]:
            if "." in t:
                parent = tree_to_ui.get(t.rsplit(".", 1)[0])
                if parent and parent != r["ui"]:
                    pairs.add((r["ui"], parent))
    return pairs


def load_mesh(client: Neo4jClient) -> None:
    path = download(settings.mesh_xml_url, settings.mesh_xml_url.rsplit("/", 1)[-1])
    with open(path, "rb") as raw:
        is_gzip = raw.read(2) == GZIP_MAGIC  # NLM may serve the ".gz" file uncompressed
    with (gzip.open if is_gzip else open)(path, "rb") as fh:
        records = list(parse_mesh_xml(fh))
    write_mesh(client, records)


def write_mesh(client: Neo4jClient, records: list[dict]) -> None:
    client.write_batches(
        "UNWIND $rows AS r MERGE (d:MeshDescriptor {ui: r.ui}) "
        "SET d.name = r.name, d.scope_note = r.scope_note, d.tree_numbers = r.tree_numbers, "
        "    d.is_disease = r.is_disease",
        ({k: r[k] for k in ("ui", "name", "scope_note", "tree_numbers", "is_disease")} for r in records),
        size=5000,
    )
    pairs = hierarchy_pairs(records)
    client.write_batches(
        "UNWIND $rows AS r MATCH (c:MeshDescriptor {ui: r.c}), (p:MeshDescriptor {ui: r.p}) "
        "MERGE (c)-[:MESH_PARENT]->(p)",
        ({"c": c, "p": p} for c, p in pairs),
        size=5000,
    )
    term_rows = [{"ui": r["ui"], "name": norm(t), "display": t} for r in records for t in r["terms"]]
    client.write_batches(
        "UNWIND $rows AS r MERGE (t:MeshEntryTerm {name: r.name}) ON CREATE SET t.display = r.display "
        "WITH t, r MATCH (d:MeshDescriptor {ui: r.ui}) MERGE (d)-[:HAS_ENTRY_TERM]->(t)",
        term_rows,
        size=5000,
    )
    print(f"mesh: {len(records)} descriptors, {len(term_rows)} entry-term links, "
          f"{len(pairs)} hierarchy relationships")
