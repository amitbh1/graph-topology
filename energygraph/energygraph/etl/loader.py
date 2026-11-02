"""Load canonical records into the graph (and prices into the time-series store).

Governance enforced here, for every source:
* the record's source must be registered, may emit that record kind, and may
  emit that entitlement (a public connector cannot write org data and vice versa);
* every node and relationship gets `source`, `entitlement`, `as_of`;
* a record never overwrites a node owned by a different entitlement — licensed
  facts about a public asset become separate, entitlement-tagged relationships
  and nodes, so public properties can never be replaced by licensed values;
* relationships are keyed by entitlement, so a public and a licensed fact about
  the same pair stay separate;
* nodes created only as a reference target are `stub`s (hidden from reads, carry only an id)
  until their own record arrives — from any source — so load order does not matter.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from ..db import Neo4jClient
from ..sources.base import SourceMeta, registry
from ..sources.records import ASSET_TYPES, Record
from ..timeseries.store import SeriesStore, get_store

logger = logging.getLogger("energygraph.loader")

OWN = "(n.entitlement = r.entitlement OR coalesce(n.stub, false))"
TAKE = "n.entitlement = r.entitlement, n.stub = false, n.source = r.source, n.as_of = r.as_of"


def _when(cond: str, body: str) -> str:
    return f"FOREACH (_ IN CASE WHEN {cond} THEN [1] ELSE [] END | {body})"


def _stub(var: str, label: str, key: str, value: str) -> str:
    return (f"MERGE ({var}:{label} {{{key}: {value}}}) "
            f"ON CREATE SET {var}.entitlement = r.entitlement, {var}.source = r.source, {var}.stub = true")


def _region(var: str, code: str, name: str = "null") -> str:
    return (f"MERGE ({var}:Region {{code: {code}}}) ON CREATE SET {var}.entitlement = 'public', "
            f"{var}.source = 'energygraph-core', {var}.kind = 'country' "
            f"SET {var}.name = coalesce({var}.name, {name}, {code})")


def _rel(a: str, rel: str, b: str, var: str = "x") -> str:
    return (f"MERGE ({a})-[{var}:{rel} {{entitlement: r.entitlement}}]->({b}) "
            f"SET {var}.source = r.source, {var}.as_of = r.as_of")


Q_CONCEPT = f"""
UNWIND $rows AS r
MERGE (n:Concept {{code: r.code}}) ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.label = r.label, n.label_lower = toLower(r.label), n.definition = r.definition, "
              "n.synonyms = r.synonyms, n.synonyms_lower = [s IN r.synonyms | toLower(s)]")}
"""
Q_CONCEPT_PARENTS = f"""
UNWIND $rows AS r UNWIND r.parents AS pc
MATCH (n:Concept {{code: r.code}}) MATCH (p:Concept {{code: pc}})
{_rel('n', 'CONCEPT_PARENT', 'p')}
"""

Q_COMPANY = f"""
UNWIND $rows AS r
MERGE (n:Company {{company_id: r.company_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.name = r.name, n.name_lower = toLower(r.name), n.lei = coalesce(r.lei, n.lei), "
              "n.country = coalesce(r.country, n.country)")}
WITH n, r
FOREACH (al IN r.aliases |
  MERGE (a:CompanyAlias {{name: toLower(al)}})
    ON CREATE SET a.display = al, a.entitlement = r.entitlement, a.source = r.source
  MERGE (a)-[x:ALIAS_OF {{entitlement: r.entitlement}}]->(n) SET x.source = r.source)
FOREACH (c IN CASE WHEN r.country IS NULL THEN [] ELSE [r.country] END |
  {_region('g', 'c')}
  MERGE (n)-[x:HEADQUARTERED_IN {{entitlement: r.entitlement}}]->(g) SET x.source = r.source)
FOREACH (pid IN CASE WHEN r.parent_company_id IS NULL THEN [] ELSE [r.parent_company_id] END |
  {_stub('p', 'Company', 'company_id', 'pid')}
  MERGE (n)-[x:SUBSIDIARY_OF {{entitlement: r.entitlement}}]->(p) SET x.source = r.source, x.as_of = r.as_of)
"""


def q_asset(label: str) -> str:
    assert label in ASSET_TYPES  # labels come from the validated Literal, never from input
    return f"""
UNWIND $rows AS r
MERGE (n:Asset {{asset_id: r.asset_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET n:{label}, {TAKE}, n.asset_type = r.asset_type, n.name = r.name, "
              "n.name_lower = toLower(r.name), n.country = r.country, n.latitude = r.latitude, "
              "n.longitude = r.longitude, n.location = CASE WHEN r.latitude IS NULL OR r.longitude IS NULL "
              "THEN null ELSE point({latitude: r.latitude, longitude: r.longitude}) END, "
              "n.capacity = r.capacity, n.capacity_unit = r.capacity_unit, n.status = r.status, "
              "n.commissioning_year = r.commissioning_year")}
WITH n, r
FOREACH (al IN r.aliases |
  MERGE (a:AssetAlias {{name: toLower(al)}})
    ON CREATE SET a.display = al, a.entitlement = r.entitlement, a.source = r.source
  MERGE (a)-[x:ASSET_ALIAS_OF {{entitlement: r.entitlement}}]->(n) SET x.source = r.source)
FOREACH (c IN CASE WHEN r.country IS NULL THEN [] ELSE [r.country] END |
  {_region('g', 'c', 'r.country_name')}
  MERGE (n)-[x:LOCATED_IN {{entitlement: r.entitlement}}]->(g) SET x.source = r.source)
FOREACH (o IN r.owners |
  {_stub('co', 'Company', 'company_id', 'o.company_id')}
  MERGE (co)-[x:OWNS {{entitlement: r.entitlement}}]->(n) SET x.share = o.share, x.source = r.source, x.as_of = r.as_of)
FOREACH (op IN CASE WHEN r.operator_id IS NULL THEN [] ELSE [r.operator_id] END |
  {_stub('oc', 'Company', 'company_id', 'op')}
  MERGE (oc)-[x:OPERATES {{entitlement: r.entitlement}}]->(n) SET x.source = r.source, x.as_of = r.as_of)
"""


Q_USES = f"""
UNWIND $rows AS r UNWIND r.concept_codes AS cc
MATCH (n:Asset {{asset_id: r.asset_id}}) MATCH (c:Concept {{code: cc}})
{_rel('n', 'USES', 'c')}
"""

Q_CONNECTION = f"""
UNWIND $rows AS r
{_stub('n', 'Asset', 'asset_id', 'r.from_asset_id')}
WITH n, r
{_stub('m', 'Asset', 'asset_id', 'r.to_asset_id')}
{_rel('n', 'CONNECTS_TO', 'm')} SET x.capacity = r.capacity, x.capacity_unit = r.capacity_unit
"""

Q_INCIDENT = f"""
UNWIND $rows AS r
MERGE (n:Incident {{incident_id: r.incident_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.incident_type = r.incident_type, n.start = date(r.start), "
              "n.end = CASE WHEN r.end IS NULL THEN null ELSE date(r.end) END, "
              "n.capacity_offline = r.capacity_offline, n.capacity_unit = r.capacity_unit, n.description = r.description")}
WITH n, r
{_stub('a', 'Asset', 'asset_id', 'r.asset_id')}
MERGE (a)-[x:HAD_INCIDENT {{entitlement: r.entitlement}}]->(n) SET x.source = r.source
"""

Q_PROJECT = f"""
UNWIND $rows AS r
MERGE (n:Project {{project_id: r.project_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.name = r.name, n.name_lower = toLower(r.name), n.asset_type = r.asset_type, "
              "n.status = r.status, n.capacity = r.capacity, n.capacity_unit = r.capacity_unit, "
              "n.country = r.country, n.expected_year = r.expected_year")}
WITH n, r
FOREACH (d IN r.developer_ids |
  {_stub('co', 'Company', 'company_id', 'd')}
  MERGE (co)-[x:DEVELOPS {{entitlement: r.entitlement}}]->(n) SET x.source = r.source)
FOREACH (c IN CASE WHEN r.country IS NULL THEN [] ELSE [r.country] END |
  {_region('g', 'c', 'r.country_name')}
  MERGE (n)-[x:PROJECT_LOCATED_IN {{entitlement: r.entitlement}}]->(g) SET x.source = r.source)
FOREACH (e IN CASE WHEN r.expands_asset_id IS NULL THEN [] ELSE [r.expands_asset_id] END |
  {_stub('a', 'Asset', 'asset_id', 'e')}
  MERGE (n)-[x:EXPANDS {{entitlement: r.entitlement}}]->(a) SET x.source = r.source)
"""

Q_PROJECT_USES = f"""
UNWIND $rows AS r UNWIND r.concept_codes AS cc
MATCH (n:Project {{project_id: r.project_id}}) MATCH (c:Concept {{code: cc}})
{_rel('n', 'PROJECT_USES', 'c')}
"""

Q_SERIES = f"""
UNWIND $rows AS r
MERGE (n:PriceSeries {{series_id: r.series_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.name = r.name, n.unit = r.unit, n.frequency = r.frequency, "
              "n.methodology_url = r.methodology_url, n.commodity = r.commodity")}
WITH n, r
FOREACH (h IN CASE WHEN r.hub_code IS NULL THEN [] ELSE [r.hub_code] END |
  MERGE (hub:Hub {{code: h}}) ON CREATE SET hub.entitlement = 'public', hub.source = r.source, hub.name = r.hub_name
  MERGE (n)-[x:PRICED_AT {{entitlement: r.entitlement}}]->(hub) SET x.source = r.source
  FOREACH (c IN CASE WHEN r.hub_country IS NULL THEN [] ELSE [r.hub_country] END |
    {_region('g', 'c')}
    MERGE (hub)-[y:HUB_IN {{entitlement: 'public'}}]->(g) SET y.source = r.source))
FOREACH (cc IN r.concept_codes |
  MERGE (c:Concept {{code: cc}})
  MERGE (n)-[x:SERIES_FOR {{entitlement: r.entitlement}}]->(c) SET x.source = r.source)
"""

Q_DOCUMENT = f"""
UNWIND $rows AS r
MERGE (n:Document {{doc_id: r.doc_id}})
  ON CREATE SET n.entitlement = r.entitlement, n.source = r.source, n.stub = true
WITH n, r, {OWN} AS own
{_when('own', f"SET {TAKE}, n.title = r.title, n.text = r.text, n.url = r.url, "
              "n.published_at = CASE WHEN r.published_at IS NULL THEN null ELSE date(r.published_at) END")}
WITH n, r
CALL (n, r) {{
  UNWIND r.asset_ids AS aid MATCH (a:Asset {{asset_id: aid}})
  MERGE (n)-[x:MENTIONS_ASSET {{entitlement: r.entitlement}}]->(a) SET x.source = r.source
}}
CALL (n, r) {{
  UNWIND r.company_ids AS cid MATCH (c:Company {{company_id: cid}})
  MERGE (n)-[x:MENTIONS_COMPANY {{entitlement: r.entitlement}}]->(c) SET x.source = r.source
}}
"""

Q_SOURCE = """
UNWIND $rows AS r
MERGE (s:DataSource {source_id: r.source_id})
SET s += r, s.entitlement = 'public', s.source = 'energygraph-core'
"""


class LoadRejected(ValueError):
    pass


@dataclass
class LoadReport:
    loaded: Counter = field(default_factory=Counter)
    rejected: Counter = field(default_factory=Counter)
    unresolved_concepts: Counter = field(default_factory=Counter)

    def __str__(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.loaded.items())]
        if self.rejected:
            parts.append("rejected: " + ", ".join(f"{k}={v}" for k, v in self.rejected.items()))
        if self.unresolved_concepts:
            parts.append(f"unresolved concepts: {dict(self.unresolved_concepts.most_common(5))}")
        return " ".join(parts) or "nothing loaded"


def check_governance(rec: Record, metas: dict[str, SourceMeta]) -> None:
    meta = metas.get(rec.source)
    if meta is None:
        raise LoadRejected(f"source {rec.source!r} is not registered")
    if not meta.allows(rec.entitlement):
        raise LoadRejected(f"source {rec.source!r} may not emit entitlement {rec.entitlement!r}")
    if meta.record_kinds and rec.kind not in meta.record_kinds:
        raise LoadRejected(f"source {rec.source!r} may not emit {rec.kind} records")


def concept_index(client: Neo4jClient) -> dict[str, str]:
    """label / synonym / code (lower-case) -> concept code; labels win over synonyms."""
    rows = client.read("MATCH (c:Concept) WHERE NOT coalesce(c.stub, false) "
                       "RETURN c.code AS code, c.label_lower AS label, c.synonyms_lower AS syn")
    index: dict[str, str] = {}
    for r in rows:
        for s in r["syn"] or []:
            index.setdefault(s, r["code"])
    for r in rows:
        if r["label"]:
            index[r["label"]] = r["code"]
        index[r["code"].lower()] = r["code"]
    return index


def register_sources(client: Neo4jClient) -> None:
    rows = [{"source_id": m.source_id, "title": m.title, "kind": m.kind, "license": m.license,
             "attribution": m.attribution, "url": m.url, "refresh": m.refresh,
             "dataset_entitlements": list(m.entitlements)}
            for m in (c.meta for c in registry().values())]
    client.write_batches(Q_SOURCE, rows)


def _row(rec: Record) -> dict:
    d = rec.model_dump(mode="json", exclude={"kind"})
    if "owners" in d:
        d["owners"] = [{"company_id": o["company_id"], "share": o.get("share")} for o in d["owners"]]
    return d


def load(client: Neo4jClient, records: Iterable[Record], store: SeriesStore | None = None,
         batch: int = 1000) -> LoadReport:
    metas = {sid: c.meta for sid, c in registry().items()}
    report = LoadReport()
    buckets: dict[str, list[Record]] = defaultdict(list)
    for rec in records:
        try:
            check_governance(rec, metas)
        except LoadRejected as exc:
            report.rejected[str(exc)] += 1
            continue
        buckets[rec.kind].append(rec)

    def write(query: str, rows: list[dict]) -> None:
        if rows:
            client.write_batches(query, rows, size=batch)

    # Order matters: concepts and companies before the records that reference them.
    concept_rows = [_row(r) for r in buckets["concept"]]
    write(Q_CONCEPT, concept_rows)
    write(Q_CONCEPT_PARENTS, [r for r in concept_rows if r["parents"]])
    write(Q_COMPANY, [_row(r) for r in buckets["company"]])

    index = concept_index(client)

    def codes(rec) -> list[str]:
        out = []
        for c in rec.concepts if hasattr(rec, "concepts") else [rec.commodity]:
            code = index.get(c.lower())
            if code:
                out.append(code)
            else:
                report.unresolved_concepts[c.lower()] += 1
        return list(dict.fromkeys(out))

    by_type: dict[str, list[dict]] = defaultdict(list)
    for rec in buckets["asset"]:
        by_type[rec.asset_type].append({**_row(rec), "concept_codes": codes(rec)})
    for label, rows in by_type.items():
        write(q_asset(label), rows)
        write(Q_USES, [r for r in rows if r["concept_codes"]])
    write(Q_CONNECTION, [_row(r) for r in buckets["connection"]])
    write(Q_INCIDENT, [_row(r) for r in buckets["incident"]])
    project_rows = [{**_row(r), "concept_codes": codes(r)} for r in buckets["project"]]
    write(Q_PROJECT, project_rows)
    write(Q_PROJECT_USES, [r for r in project_rows if r["concept_codes"]])
    write(Q_SERIES, [{**_row(r), "concept_codes": codes(r)} for r in buckets["price_series"]])
    write(Q_DOCUMENT, [_row(r) for r in buckets["document"]])
    if buckets["price"]:
        (store or get_store()).upsert((r.series_id, r.date, r.value, r.source, r.entitlement)
                                      for r in buckets["price"])

    for kind, recs in buckets.items():
        report.loaded[kind] += len(recs)
    return report
