"""Embedding backends.

* OllamaEmbedder  — local `nomic-embed-text` (default, semantic).
* HashingEmbedder — deterministic character n-gram hashing. No model needed;
  lexical rather than semantic. Used for offline demos and tests.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import requests

from .config import settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    # nomic-embed-text is trained with task prefixes.
    DOC_PREFIX = "search_document: "
    QUERY_PREFIX = "search_query: "

    def __init__(self, model: str | None = None, base_url: str | None = None, dim: int | None = None):
        self.model = model or settings.embedding_model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.dim = dim or settings.embedding_dim
        self._prefix = "nomic" in self.model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts, "truncate": True},
            timeout=300,
        )
        resp.raise_for_status()
        vectors = resp.json()["embeddings"]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"{self.model} returned {len(vectors[0])}-d vectors; set EMBEDDING_DIM={len(vectors[0])}"
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([(self.DOC_PREFIX if self._prefix else "") + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([(self.QUERY_PREFIX if self._prefix else "") + text])[0]


class HashingEmbedder:
    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embedding_dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        words = re.findall(r"[a-z0-9]+", text.lower())
        feats = list(words)
        for w in words:
            padded = f"#{w}#"
            feats += [padded[i:i + 3] for i in range(len(padded) - 2)]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embedder() -> Embedder:
    if settings.embedding_backend == "hash":
        return HashingEmbedder()
    return OllamaEmbedder()
