"""Embed every ontology class that declares mg:embedText and store vectors in Neo4j."""

from __future__ import annotations

from tqdm import tqdm

from ..db import Neo4jClient
from ..embeddings import Embedder, get_embedder
from ..ontology import NodeClass, get_ontology


def embed_text(cls: NodeClass, props: dict) -> str:
    parts = [str(props.get(p) or "") for p in cls.embed_text]
    return " — ".join(p for p in parts if p)[:2000]


def embed_class(client: Neo4jClient, cls: NodeClass, embedder: Embedder,
                batch: int = 256, force: bool = False) -> int:
    where = "" if force else "WHERE n.embedding IS NULL"
    fields = ", ".join(f"{p}: n.{p}" for p in cls.embed_text)
    rows = client.read(f"MATCH (n:{cls.name}) {where} RETURN n.{cls.key} AS key, {{{fields}}} AS props")
    for i in tqdm(range(0, len(rows), batch), desc=f"embed {cls.name}", leave=False):
        chunk = rows[i:i + batch]
        texts = [embed_text(cls, r["props"]) or str(r["key"]) for r in chunk]
        vectors = embedder.embed_documents(texts)
        client.write(
            f"UNWIND $rows AS r MATCH (n:{cls.name} {{{cls.key}: r.key}}) "
            "CALL db.create.setNodeVectorProperty(n, 'embedding', r.vec)",
            rows=[{"key": r["key"], "vec": v} for r, v in zip(chunk, vectors)],
        )
    return len(rows)


def embed_all(client: Neo4jClient, force: bool = False, only: list[str] | None = None) -> None:
    embedder = get_embedder()
    total = 0
    for cls in get_ontology().embedded_classes:
        if only and cls.name not in only:
            continue
        n = embed_class(client, cls, embedder, force=force)
        total += n
        print(f"embed: {cls.name:16s} {n}")
    print(f"embed: {total} embeddings written")
