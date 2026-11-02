"""ETL orchestration: run registered connectors into the loader in bounded chunks."""

from __future__ import annotations

import itertools
import logging
from collections import Counter
import time

from ..config import settings
from ..db import Neo4jClient
from ..observability import log
from ..schema import create_schema
from ..sources.base import SourceConnector, registry
from .loader import load, register_sources

logger = logging.getLogger("energygraph.etl")

PUBLIC_ORDER = ["energygraph-core", "oeo", "gppd", "wikidata", "gleif", "eia"]
CHUNK = 20_000


def _gleif_connector(client: Neo4jClient) -> SourceConnector:
    """Resolve the most-connected named companies that have no LEI yet."""
    from ..sources.public.gleif import GleifResolver

    rows = client.read(
        "MATCH (c:Company)-[:OWNS|OPERATES]->(a:Asset) WHERE c.lei IS NULL AND c.entitlement = 'public' "
        "AND NOT coalesce(c.stub, false) RETURN c.company_id AS id, c.name AS name, count(a) AS n "
        "ORDER BY n DESC LIMIT $k", k=settings.gleif_max_companies)
    return GleifResolver((r["id"], r["name"]) for r in rows)


def run_source(client: Neo4jClient, source_id: str, limit: int | None = None) -> str:
    connector = registry().get(source_id)
    if connector is None:
        raise SystemExit(f"unknown source {source_id!r}; see `energygraph sources`")
    if source_id == "gleif":
        connector = _gleif_connector(client)
    ok, reason = connector.available()
    if not ok:
        return f"{source_id}: skipped — {reason}"
    start = time.perf_counter()
    totals = []
    try:
        it = connector.fetch(limit)
        while chunk := list(itertools.islice(it, CHUNK)):
            totals.append(load(client, chunk))
    except Exception as exc:  # never print raw messages: URLs may carry API keys
        log(logger, logging.ERROR, "source failed", source=source_id, error=type(exc).__name__)
        return f"{source_id}: FAILED ({type(exc).__name__})"
    loaded = sum((t.loaded for t in totals), Counter())
    rejected = sum((t.rejected for t in totals), Counter())
    unresolved = sum((t.unresolved_concepts for t in totals), Counter())
    quarantined = len(getattr(connector, "rejected", []))
    msg = f"{source_id}: {dict(loaded)} in {time.perf_counter() - start:.1f}s"
    if rejected:
        msg += f" | governance-rejected {dict(rejected)}"
    if quarantined:
        msg += f" | quarantined {quarantined} invalid records (data/quarantine/)"
    if unresolved:
        msg += f" | unresolved concepts {dict(unresolved.most_common(5))}"
    return msg


def run(client: Neo4jClient, steps: list[str], limit: int | None = None) -> None:
    if steps == ["all"]:
        steps = ["schema", *PUBLIC_ORDER, "org", "similarity", "embed"]
    for step in steps:
        if step == "schema":
            create_schema(client)
            register_sources(client)
            print("schema: ready")
        elif step == "org":
            for sid in [s for s in registry() if s.startswith("org-")]:
                print(run_source(client, sid, limit))
        elif step == "similarity":
            from .similarity import compute_similarity

            compute_similarity(client)
        elif step == "embed":
            from .embed import embed_all

            embed_all(client)
        else:
            print(run_source(client, step, limit))
