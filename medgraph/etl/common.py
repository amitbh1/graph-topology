"""Shared ETL helpers: cached HTTP, name normalisation, dictionary term matching."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

import requests

from ..config import settings

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "MedGraph/1.0 (local research ETL)"


def norm(text: str | None) -> str:
    """Canonical key form: lower-case, single-spaced, trimmed."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def lucene_value(value: str) -> str:
    """Quote a value for an openFDA (Lucene) search expression: no quotes/operators can escape it."""
    cleaned = re.sub(r'[\\"+\-!(){}\[\]^~*?:/|&]', " ", value)
    return '"' + re.sub(r"\s+", " ", cleaned).strip() + '"'


def get_json(url: str, params: dict | None = None, *, cache: bool = True,
             retries: int = 4, allow_404: bool = True) -> Any:
    """GET JSON with an on-disk cache (data/cache) and exponential back-off."""
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    path = settings.cache_dir / "http" / f"{key}.json"
    if cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    for attempt in range(retries):
        resp = SESSION.get(url, params=params, timeout=60)
        if resp.status_code == 404 and allow_404:
            data = None
            break
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        resp.raise_for_status()
        data = None

    if cache:
        # Best effort and atomic: a failed cache write (e.g. a Windows file lock) must not stop the ETL.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{time.time_ns()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
    return data


def download(url: str, filename: str) -> Path:
    """Download a large file once into data/raw."""
    path = settings.data_dir / "raw" / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    with SESSION.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(path)
    return path


_BRITISH = [
    ("oesoph", "esoph"), ("oedema", "edema"), ("aemia", "emia"), ("haem", "hem"), ("oea", "ea"),
    ("anaes", "anes"), ("paed", "ped"), ("foet", "fet"), ("tumour", "tumor"), ("colour", "color"),
    ("isation", "ization"), ("ischaem", "ischem"), ("leukaem", "leukem"), ("coeli", "celi"),
]


def americanize(term: str) -> str:
    """MedDRA uses British spelling ('hypoglycaemia'); MeSH uses American ('hypoglycemia')."""
    for gb, us in _BRITISH:
        term = term.replace(gb, us)
    return term


_NEGATION = re.compile(
    r"\b(not (be )?(for|indicated|recommended|used)|should not|limitations? of use|contraindicated|"
    r"has not been (studied|established)|not been shown)\b", re.IGNORECASE)


def affirmative_sentences(text: str) -> str:
    """Drop sentences such as 'Not for treatment of type 1 diabetes' before indication extraction."""
    sentences = re.split(r"(?<=[.;])\s+", text or "")
    return " ".join(s for s in sentences if not _NEGATION.search(s))


_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


class TermMatcher:
    """Longest-match dictionary lookup of multi-word terms in free text."""

    def __init__(self, terms: Iterable[tuple[str, Any]], max_words: int = 8, min_chars: int = 4):
        self.lookup: dict[tuple[str, ...], Any] = {}
        self.max_words = max_words
        for term, value in terms:
            words = tuple(_WORD.findall(term.lower()))
            if words and len(words) <= max_words and len(" ".join(words)) >= min_chars:
                self.lookup.setdefault(words, value)

    def find(self, text: str) -> list[tuple[str, Any]]:
        words = _WORD.findall((text or "").lower())
        found: dict[str, Any] = {}
        i = 0
        while i < len(words):
            for n in range(min(self.max_words, len(words) - i), 0, -1):
                key = tuple(words[i:i + n])
                if key in self.lookup:
                    found.setdefault(" ".join(key), self.lookup[key])
                    i += n
                    break
            else:
                i += 1
        return list(found.items())
