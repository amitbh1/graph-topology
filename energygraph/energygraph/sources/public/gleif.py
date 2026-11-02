"""GLEIF LEI -> legal-entity resolution and parent companies.

Licence: CC0 1.0. Matching is deliberately conservative: a company is linked to
an LEI only when the normalised names are identical (legal suffixes removed).
A wrong ownership link is worse than a missing one.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Iterator

from ...etl.common import get_json, norm
from ..base import SourceConnector, SourceMeta
from ..records import CompanyRecord

API = "https://api.gleif.org/api/v1"
SUFFIXES = r"\b(inc|incorporated|llc|l\.?l\.?c|ltd|limited|plc|corp|corporation|co|company|sa|s\.a|ag|gmbh|" \
           r"spa|s\.p\.a|nv|n\.v|bv|b\.v|lp|l\.p|ab|asa|oyj|as|pjsc|jsc|ojsc|pte|pty|sas|srl|kk|the)\b"


def legal_key(name: str) -> str:
    text = re.sub(r"[.,&()'/-]", " ", norm(name))
    text = re.sub(SUFFIXES, " ", text)
    return re.sub(r"\s+", " ", text).strip()


def best_match(name: str, candidates: list[dict]) -> tuple[str, str] | None:
    key = legal_key(name)
    for c in candidates:
        value = c.get("attributes", {}).get("value", "")
        lei = c.get("relationships", {}).get("lei-records", {}).get("data", {}).get("id")
        if lei and key and legal_key(value) == key:
            return lei, value
    return None


def _parent(lei: str, kind: str) -> tuple[str, str] | None:
    data = get_json(f"{API}/lei-records/{lei}/{kind}") or {}
    rec = data.get("data") or {}
    name = rec.get("attributes", {}).get("entity", {}).get("legalName", {}).get("name")
    return (rec["id"], name) if rec.get("id") and name else None


class GleifResolver(SourceConnector):
    meta = SourceMeta(
        source_id="gleif", title="GLEIF Legal Entity Identifier records", kind="public", license="CC0 1.0",
        attribution="Global Legal Entity Identifier Foundation (GLEIF)", url="https://www.gleif.org/",
        refresh="daily", record_kinds=("company",),
    )

    def __init__(self, companies: Iterable[tuple[str, str]] = ()):
        self.companies = list(companies)  # (company_id, name) pairs to resolve

    def fetch(self, limit: int | None = None) -> Iterator[CompanyRecord]:
        today = dt.date.today()
        for i, (company_id, name) in enumerate(self.companies):
            if limit and i >= limit:
                break
            data = get_json(f"{API}/fuzzycompletions", {"field": "entity.legalName", "q": name[:100]}) or {}
            match = best_match(name, data.get("data", []))
            if not match:
                continue
            lei, legal_name = match
            parent = _parent(lei, "direct-parent")
            if parent:
                yield CompanyRecord(source=self.meta.source_id, company_id=f"lei:{parent[0]}", name=parent[1][:300],
                                    lei=parent[0], as_of=today)
            yield CompanyRecord(source=self.meta.source_id, company_id=company_id, name=name[:300], lei=lei,
                                aliases=[legal_name[:300]] if legal_name != name else [],
                                parent_company_id=f"lei:{parent[0]}" if parent else None, as_of=today)
