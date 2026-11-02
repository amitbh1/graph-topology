"""RxNorm / RxNav drug identity: canonical ingredients and brand-name aliases."""

from __future__ import annotations

from functools import lru_cache

from ..db import Neo4jClient
from .common import get_json, norm

RXNAV = "https://rxnav.nlm.nih.gov/REST"


@lru_cache(maxsize=None)
def rx_lookup(name: str) -> str | None:
    data = get_json(f"{RXNAV}/rxcui.json", {"name": name, "search": 2}) or {}
    ids = (data.get("idGroup") or {}).get("rxnormId") or []
    return ids[0] if ids else None


@lru_cache(maxsize=None)
def rx_properties(rxcui: str) -> dict:
    data = get_json(f"{RXNAV}/rxcui/{rxcui}/properties.json") or {}
    return data.get("properties") or {}


@lru_cache(maxsize=None)
def rx_related(rxcui: str, ttys: str) -> tuple[dict, ...]:
    """`ttys` is space-separated (sent as 'IN+MIN'; a literal '+' would be encoded as %2B)."""
    try:
        data = get_json(f"{RXNAV}/rxcui/{rxcui}/related.json", {"tty": ttys}) or {}
    except Exception:
        return ()
    out = []
    for group in (data.get("relatedGroup") or {}).get("conceptGroup") or []:
        out += group.get("conceptProperties") or []
    return tuple(out)


def canonical_ingredient(name: str) -> tuple[str, str | None]:
    """Map a label generic name (often a salt, e.g. 'metformin hydrochloride')
    to its RxNorm ingredient ('metformin'). Falls back to the normalised name."""
    try:
        rxcui = rx_lookup(name)
    except Exception:
        rxcui = None
    if not rxcui:
        return norm(name), None
    try:
        props = rx_properties(rxcui)
    except Exception:
        props = {}
    if props.get("tty") in ("IN", "MIN"):
        return norm(props["name"]), rxcui
    related = rx_related(rxcui, "IN MIN")
    mins = [c for c in related if c.get("tty") == "MIN"]
    ins = [c for c in related if c.get("tty") == "IN"]
    # A salt's related MINs include every combination containing it, so a single IN wins;
    # a MIN is only used when the concept itself has several ingredients.
    if len(ins) == 1:
        pick = ins[0]
    elif len(ins) > 1 and len(mins) == 1:
        pick = mins[0]
    else:
        pick = None
    if pick:
        return norm(pick["name"]), pick["rxcui"]
    return norm(name), rxcui


def load_brand_aliases(client: Neo4jClient) -> None:
    """Add RxNorm brand names (tty=BN) as aliases of every drug with an rxcui."""
    drugs = client.read("MATCH (d:Drug) WHERE d.rxcui IS NOT NULL RETURN d.name AS name, d.rxcui AS rxcui")
    rows = []
    for d in drugs:
        for c in rx_related(d["rxcui"], "BN"):
            if norm(c["name"]) != d["name"]:
                rows.append({"drug": d["name"], "name": norm(c["name"]), "display": c["name"],
                             "rxcui": c["rxcui"]})
    client.write_batches(
        "UNWIND $rows AS r MATCH (d:Drug {name: r.drug}) "
        "MERGE (a:DrugAlias {name: r.name}) "
        "ON CREATE SET a.display = r.display, a.kind = 'brand', a.source = 'rxnorm', a.rxcui = r.rxcui "
        "MERGE (a)-[:ALIAS_OF]->(d)",
        rows,
    )
    total = client.read("MATCH (a:DrugAlias) RETURN count(a) AS n")[0]["n"]
    print(f"rxnorm: {len(rows)} brand links, {total} aliases in graph")
