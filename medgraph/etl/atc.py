"""WHO ATC classification -> (:ATCLevel)-[:ATC_PARENT]->(:ATCLevel) and Drug links.

ATC codes have five levels with fixed code lengths:
    C        level 1  anatomical main group
    C03      level 2  therapeutic subgroup
    C03X     level 3  pharmacological subgroup
    C03XA    level 4  chemical subgroup
    C03XA01  level 5  chemical substance
"""

from __future__ import annotations

import csv

from ..config import settings
from ..db import Neo4jClient
from .common import download, norm

LEVEL_BY_LENGTH = {1: 1, 3: 2, 4: 3, 5: 4, 7: 5}
PARENT_LENGTH = {3: 1, 4: 3, 5: 4, 7: 5}


def atc_level(code: str) -> int:
    try:
        return LEVEL_BY_LENGTH[len(code)]
    except KeyError:
        raise ValueError(f"not an ATC code: {code!r}") from None


def atc_parent(code: str) -> str | None:
    n = PARENT_LENGTH.get(len(code))
    return code[:n] if n else None


def atc_path(code: str) -> list[str]:
    """All ancestor codes from level 1 down to `code` itself."""
    return [code[:n] for n in (1, 3, 4, 5, 7) if n <= len(code)]


def read_atc_csv(path) -> list[dict]:
    seen: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = row["atc_code"].strip().upper()
            if len(code) in LEVEL_BY_LENGTH and code not in seen:
                seen[code] = {
                    "code": code,
                    "name": row["atc_name"].strip(),
                    "level": atc_level(code),
                    "parent": atc_parent(code),
                }
    return list(seen.values())


def load_atc(client: Neo4jClient) -> None:
    write_atc(client, read_atc_csv(download(settings.atc_csv_url, "atc.csv")))


def write_atc(client: Neo4jClient, rows: list[dict]) -> None:
    client.write_batches(
        "UNWIND $rows AS r MERGE (a:ATCLevel {code: r.code}) "
        "SET a.name = r.name, a.level = r.level, a.name_lower = toLower(r.name)",
        rows,
    )
    client.write_batches(
        "UNWIND $rows AS r MATCH (c:ATCLevel {code: r.code}), (p:ATCLevel {code: r.parent}) "
        "MERGE (c)-[:ATC_PARENT]->(p)",
        [r for r in rows if r["parent"]],
    )
    print(f"atc: {len(rows)} levels")


def link_drugs_to_atc(client: Neo4jClient) -> None:
    """Drug -> level-5 ATC code by substance name (also via aliases, e.g. salt forms)."""
    client.write("CREATE INDEX atclevel_name_lower IF NOT EXISTS FOR (a:ATCLevel) ON (a.name_lower)")
    client.write("CALL db.awaitIndexes(120)")
    res = client.write(
        "MATCH (a:ATCLevel {level: 5}) "
        "MATCH (d:Drug) WHERE d.name = a.name_lower "
        "   OR EXISTS { (:DrugAlias {name: a.name_lower})-[:ALIAS_OF]->(d) } "
        "MERGE (d)-[:CLASSIFIED_AS]->(a) RETURN count(*) AS n"
    )
    print(f"atc: {res[0]['n']} drug classifications")


__all__ = ["atc_level", "atc_parent", "atc_path", "load_atc", "link_drugs_to_atc", "norm"]
