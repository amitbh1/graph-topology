"""Shared ETL helpers: cached HTTP with retries, downloads, name normalisation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from ..config import settings

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "EnergyGraph/1.0 (energy knowledge graph ETL; contact: data-team)"

# Query-string parameters that must never reach the cache key or a log line in clear text.
SECRET_PARAMS = ("api_key", "token", "access_token", "apikey")


def norm(text: str | None) -> str:
    """Canonical key form: lower-case, single-spaced, trimmed."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _cache_key(url: str, params: dict | None) -> str:
    safe = {k: ("<secret>" if k.lower() in SECRET_PARAMS else v) for k, v in (params or {}).items()}
    return hashlib.sha1((url + json.dumps(safe, sort_keys=True, default=str)).encode()).hexdigest()


def get_json(url: str, params: dict | None = None, *, cache: bool = True, retries: int = 4,
             allow_404: bool = True, timeout: float = 60) -> Any:
    """GET JSON with an on-disk cache (data/cache/http) and exponential back-off on 429/5xx."""
    path = settings.cache_dir / "http" / f"{_cache_key(url, params)}.json"
    if cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = None
    for attempt in range(retries):
        resp = SESSION.get(url, params=params, timeout=timeout,
                           headers={"Accept": "application/sparql-results+json, application/json"})
        if resp.status_code == 404 and allow_404:
            break
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(30, 2 ** attempt))
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        raise RuntimeError(f"{url.split('?')[0]} unavailable after {retries} attempts")
    if cache:
        # Best effort and atomic: a failed cache write must never stop the ETL.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{time.time_ns()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
    return data


def download(url: str, filename: str) -> Path:
    """Download a large file once into data/raw (atomic rename)."""
    path = settings.data_dir / "raw" / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url.split('?')[0]}")
    with SESSION.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(path)
    return path
