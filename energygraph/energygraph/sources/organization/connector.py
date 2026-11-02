"""organization-source connectors — the integration point for organization-name datasets.

The organization's datasets are not wired in yet. This module defines the
contract they must meet, so integrating one later is configuration, not code:

    ORG_SOURCE_MODE=file   ORG_SOURCE_DIR=/path/with/<dataset>.jsonl files
    ORG_SOURCE_MODE=http   ORG_SOURCE_BASE_URL=https://...  ORG_SOURCE_TOKEN=...

HTTP contract (per dataset, cursor-paginated):

    GET {base}/v1/datasets/{dataset}/records?limit=500&cursor=<opaque>
    Authorization: Bearer <token>
    200 -> {"records": [<canonical record>, ...], "next_cursor": "<opaque>" | null}

Every record must validate against the canonical models in sources/records.py
for the kinds allowed for that dataset (DATASETS below). The connector, not the
payload, decides `source` and `entitlement` (org:<dataset>): an organization
feed can never publish data as public. Invalid records are quarantined, never
loaded.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterator, Protocol

import requests
from pydantic import TypeAdapter, ValidationError

from ...config import settings
from ..base import SourceConnector, SourceMeta
from ..records import AnyRecord, Record

logger = logging.getLogger("energygraph.org")

ORG_NAME = "organization-name"

# dataset -> (title, record kinds allowed)
DATASETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "companies": ("Companies and corporate hierarchy", ("company",)),
    "power-assets": ("Power plants and units", ("asset", "company")),
    "refinery-assets": ("Refineries and capacities", ("asset", "company")),
    "lng-assets": ("LNG terminals and trains", ("asset", "company")),
    "pipelines": ("Pipelines and network connections", ("asset", "connection", "company")),
    "upstream": ("Fields and basins", ("asset", "company")),
    "outages": ("Outages and incidents", ("incident",)),
    "projects": ("Project pipeline", ("project", "company")),
    "price-series": ("Price series definitions and methodology", ("price_series",)),
    "prices": ("Published price observations", ("price",)),
    "news": ("News and commentary", ("document",)),
    "taxonomy": ("Commodity and grade taxonomy", ("concept",)),
}

MAX_PAGES = 10_000
MAX_PAGE_BYTES = 20 * 1024 * 1024
_adapter = TypeAdapter(AnyRecord)


class Transport(Protocol):
    def pages(self, dataset: str, page_size: int) -> Iterator[list[dict]]: ...

    def check(self, dataset: str) -> tuple[bool, str]: ...


class FileTransport:
    """Reads <dir>/<dataset>.jsonl — for drops, contract tests and the synthetic samples."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _path(self, dataset: str) -> Path:
        return self.dir / f"{dataset}.jsonl"

    def check(self, dataset: str) -> tuple[bool, str]:
        p = self._path(dataset)
        return (True, str(p)) if p.is_file() else (False, f"{p.name} not found")

    def pages(self, dataset: str, page_size: int) -> Iterator[list[dict]]:
        page: list[dict] = []
        with open(self._path(dataset), encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    page.append(json.loads(line))
                except json.JSONDecodeError:
                    page.append({"__invalid__": f"line {line_no}: not JSON"})
                if len(page) >= page_size:
                    yield page
                    page = []
        if page:
            yield page


class HttpTransport:
    """HTTPS, bearer token, timeouts, retries with back-off, page and size caps."""

    def __init__(self, base_url: str, token: str, timeout_s: float):
        if not base_url.startswith("https://"):
            raise ValueError("organization-source base URL must use https")
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json",
                                     "User-Agent": "EnergyGraph/1.0"})
        self.timeout = timeout_s

    def _get(self, url: str, params: dict) -> dict:
        for attempt in range(4):
            resp = self.session.get(url, params=params, timeout=self.timeout, stream=True)
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(min(30, int(resp.headers.get("Retry-After", 2 ** attempt))))
                continue
            if resp.status_code in (401, 403):
                raise PermissionError(f"organization-source rejected credentials ({resp.status_code})")
            resp.raise_for_status()
            body = resp.raw.read(MAX_PAGE_BYTES + 1, decode_content=True)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError("organization-source page exceeds size limit")
            return json.loads(body)
        raise RuntimeError("organization-source unavailable after retries")

    def check(self, dataset: str) -> tuple[bool, str]:
        try:
            self._get(f"{self.base}/v1/datasets/{dataset}/records", {"limit": 1})
            return True, "reachable"
        except Exception as exc:  # report the class only: messages may contain URLs
            return False, type(exc).__name__

    def pages(self, dataset: str, page_size: int) -> Iterator[list[dict]]:
        cursor = None
        for _ in range(MAX_PAGES):
            params = {"limit": page_size, **({"cursor": cursor} if cursor else {})}
            data = self._get(f"{self.base}/v1/datasets/{dataset}/records", params)
            records = data.get("records")
            if not isinstance(records, list):
                raise ValueError("organization-source response has no 'records' list")
            yield records
            cursor = data.get("next_cursor")
            if not cursor:
                return
        raise RuntimeError("organization-source pagination exceeded MAX_PAGES")


def transport_from_settings() -> Transport | None:
    if settings.org_source_mode == "file" and settings.org_source_dir:
        return FileTransport(settings.org_source_dir)
    if settings.org_source_mode == "http" and settings.org_source_base_url:
        return HttpTransport(settings.org_source_base_url, settings.org_source_token, settings.org_source_timeout_s)
    return None


def validate_record(raw: dict, *, dataset: str, source_id: str) -> Record:
    """Validate one payload record; source/entitlement are imposed, not trusted."""
    if not isinstance(raw, dict) or "__invalid__" in raw:
        raise ValueError(raw.get("__invalid__", "record is not an object") if isinstance(raw, dict) else "not an object")
    allowed = DATASETS[dataset][1]
    if raw.get("kind") not in allowed:
        raise ValueError(f"kind {raw.get('kind')!r} not allowed in dataset {dataset} (allowed: {', '.join(allowed)})")
    for forbidden in ("source", "entitlement"):
        if forbidden in raw and raw[forbidden] not in (source_id, f"org:{dataset}"):
            raise ValueError(f"payload may not set {forbidden}")
    payload = {**raw, "source": source_id, "entitlement": f"org:{dataset}"}
    return _adapter.validate_python(payload)


class OrganizationSource(SourceConnector):
    """One connector per organization dataset (source id `org-<dataset>`)."""

    def __init__(self, dataset: str, transport: Transport | None = None, page_size: int = 500):
        if dataset not in DATASETS:
            raise ValueError(f"unknown organization dataset {dataset!r}")
        title, kinds = DATASETS[dataset]
        self.dataset = dataset
        self.transport = transport
        self.page_size = page_size
        self.rejected: list[dict] = []
        self.meta = SourceMeta(
            source_id=f"org-{dataset}", title=f"{ORG_NAME}: {title}", kind="organization",
            license=f"{ORG_NAME} licence — redistribution per contract", attribution=ORG_NAME,
            url="", refresh="per contract", entitlements=(f"org:{dataset}",), record_kinds=kinds,
            requires=("ORG_SOURCE_MODE",),
        )

    def available(self) -> tuple[bool, str]:
        transport = self.transport or transport_from_settings()
        if transport is None:
            return False, "organization-source not configured (ORG_SOURCE_MODE)"
        return transport.check(self.dataset)

    def fetch(self, limit: int | None = None) -> Iterator[Record]:
        transport = self.transport or transport_from_settings()
        if transport is None:
            return
        n = 0
        for page in transport.pages(self.dataset, self.page_size):
            for i, raw in enumerate(page):
                try:
                    rec = validate_record(raw, dataset=self.dataset, source_id=self.meta.source_id)
                except (ValidationError, ValueError) as exc:
                    self._quarantine(raw, exc)
                    continue
                yield rec
                n += 1
                if limit and n >= limit:
                    return

    def _quarantine(self, raw, exc: Exception) -> None:
        errors = exc.errors(include_input=False) if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        entry = {"dataset": self.dataset, "errors": [{"loc": ".".join(map(str, e.get("loc", []))), "msg": e["msg"]}
                                                      for e in errors][:10],
                 "record_id": next((raw.get(k) for k in ("asset_id", "company_id", "incident_id", "project_id",
                                                          "series_id", "doc_id", "code") if isinstance(raw, dict)
                                    and raw.get(k)), None)}
        self.rejected.append(entry)
        path = settings.data_dir / "quarantine" / f"{self.meta.source_id}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            logger.warning("could not write quarantine file for %s", self.meta.source_id)


def validate_file(path: str | Path) -> dict:
    """Contract check for a sample drop: <dataset>.jsonl -> counts of valid/invalid records."""
    path = Path(path)
    dataset = path.stem
    if dataset not in DATASETS:
        return {"file": path.name, "error": f"unknown dataset; expected one of {', '.join(DATASETS)}"}
    conn = OrganizationSource(dataset, FileTransport(path.parent))
    valid = sum(1 for _ in conn.fetch())
    return {"file": path.name, "dataset": dataset, "valid": valid, "invalid": len(conn.rejected),
            "errors": conn.rejected[:20]}
