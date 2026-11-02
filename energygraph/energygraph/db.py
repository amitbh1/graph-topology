"""Neo4j clients.

* `get_client()`      read/write client for ETL and CLI.
* `get_read_client()` read-only client for the API and assistant: writes raise
  locally, queries run in READ transactions (rejected server-side), with a
  server-side timeout. Uses NEO4J_READ_USER when configured (Enterprise RBAC).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator

from neo4j import GraphDatabase, Query, RoutingControl

from .config import settings

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class ReadOnlyError(PermissionError):
    pass


class Neo4jClient:
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None,
                 database: str | None = None, read_only: bool = False, timeout_s: float | None = None):
        self.driver = GraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(user or settings.neo4j_user, password or settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_pool_size,
            connection_acquisition_timeout=10.0,
        )
        self.database = database or settings.neo4j_database
        self.read_only = read_only
        self.timeout_s = timeout_s

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def ping(self) -> bool:
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def _query(self, text: str, timeout_s: float | None) -> Query:
        t = timeout_s if timeout_s is not None else self.timeout_s
        return Query(text, timeout=t) if t else Query(text)

    def read(self, query: str, *, timeout_s: float | None = None, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(
            self._query(query, timeout_s), params, database_=self.database, routing_=RoutingControl.READ)
        return [r.data() for r in records]

    def write(self, query: str, **params: Any) -> list[dict]:
        if self.read_only:
            raise ReadOnlyError("this client is read-only")
        records, _, _ = self.driver.execute_query(self._query(query, None), params, database_=self.database)
        return [r.data() for r in records]

    def write_batches(self, query: str, rows: Iterable[dict], size: int = 2000) -> int:
        total = 0
        for chunk in _chunks(rows, size):
            self.write(query, rows=chunk)
            total += len(chunk)
        return total


def _chunks(rows: Iterable[dict], size: int) -> Iterator[list[dict]]:
    buf: list[dict] = []
    for row in rows:
        buf.append(row)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


_client: Neo4jClient | None = None
_read_client: Neo4jClient | None = None


def get_client() -> Neo4jClient:
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client


def get_read_client() -> Neo4jClient:
    global _read_client
    if _read_client is None:
        _read_client = Neo4jClient(user=settings.neo4j_read_user or None,
                                   password=settings.neo4j_read_password or None,
                                   read_only=True, timeout_s=settings.query_timeout_s)
    return _read_client


def close_read_client() -> None:
    """Close and forget the shared read client (API shutdown); the next call reconnects."""
    global _read_client
    if _read_client is not None:
        _read_client.close()
        _read_client = None
