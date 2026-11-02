"""Embed every ontology class that declares eg:embedText; vectors stored on the nodes."""

from __future__ import annotations

from ..db import Neo4jClient
from ..embeddings import Embedder, get_embedder
from ..ontology import NodeClass, get_ontology


def embed_text(cls: NodeClass, props: dict) -> str:
    return " — ".join(str(props.get(p)) for p in cls.embed_text if props.get(p))[:2000]


def embed_class(client: Neo4jClient, cls: NodeClass, embedder: Embedder, batch: int = 256,
                force: bool = False) -> int:
    where = "WHERE NOT coalesce(n.stub, false)" + ("" if force else " AND n.embedding IS NULL")
    fields = ", ".join(f"{p}: n.{p}" for p in cls.embed_text)
    rows = client.read(f"MATCH (n:{cls.name}) {where} RETURN n.{cls.key} AS key, {{{fields}}} AS props")
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        vectors = embedder.embed_documents([embed_text(cls, r["props"]) or str(r["key"]) for r in chunk])
        client.write(f"UNWIND $rows AS r MATCH (n:{cls.name} {{{cls.key}: r.key}}) "
                     "CALL db.create.setNodeVectorProperty(n, 'embedding', r.vec)",
                     rows=[{"key": r["key"], "vec": v} for r, v in zip(chunk, vectors)])
    return len(rows)


def embed_all(client: Neo4jClient, force: bool = False) -> None:
    embedder = get_embedder()
    total = 0
    for cls in get_ontology().embedded_classes:
        n = embed_class(client, cls, embedder, force=force)
        total += n
        print(f"embed: {cls.name:14s} {n}")
    print(f"embed: {total} embeddings written")
