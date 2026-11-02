"""Source connector interface and the source registry.

A connector declares its metadata (licence, attribution, entitlement, kind)
and yields canonical records. The loader refuses records from unregistered
sources or with an entitlement the source is not allowed to emit — a public
connector cannot publish organization data and vice versa.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterator, Literal

from .records import Record


@dataclass(frozen=True)
class SourceMeta:
    source_id: str
    title: str
    kind: Literal["public", "organization", "internal"]
    license: str
    attribution: str
    url: str
    refresh: str                                  # e.g. "annual", "daily", "on demand"
    entitlements: tuple[str, ...] = ("public",)   # entitlements this source may emit
    requires: tuple[str, ...] = ()                # env vars needed (e.g. API keys)
    record_kinds: tuple[str, ...] = field(default_factory=tuple)

    def allows(self, entitlement: str) -> bool:
        return entitlement in self.entitlements


class SourceConnector(abc.ABC):
    meta: SourceMeta

    def available(self) -> tuple[bool, str]:
        """(usable, reason) — e.g. False when an API key is not configured."""
        return True, "ok"

    @abc.abstractmethod
    def fetch(self, limit: int | None = None) -> Iterator[Record]:
        """Yield canonical records. Must be idempotent and safe to re-run."""


_REGISTRY: dict[str, SourceConnector] = {}


def register(connector: SourceConnector) -> SourceConnector:
    _REGISTRY[connector.meta.source_id] = connector
    return connector


def registry() -> dict[str, SourceConnector]:
    if not _REGISTRY:
        from . import load_all  # noqa: F401  (registers built-in connectors)
        load_all.register_builtin()
    return _REGISTRY


def meta_for(source_id: str) -> SourceMeta | None:
    c = registry().get(source_id)
    return c.meta if c else None
