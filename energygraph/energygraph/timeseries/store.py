"""Time-series store for published price observations.

`SeriesStore` is the interface; `SQLiteSeriesStore` is the bundled
implementation (single file, WAL mode). For production volumes, implement the
same interface over TimescaleDB / an internal series platform and select it via
TIMESERIES_URL. The graph holds only (:PriceSeries) pointers.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Protocol

from ..config import ROOT, settings


class SeriesStore(Protocol):
    def upsert(self, rows: Iterable[tuple[str, dt.date, float, str, str]]) -> int: ...

    def observations(self, series_id: str, start: dt.date | None, end: dt.date | None,
                     limit: int) -> list[tuple[str, float]]: ...

    def summary(self, series_id: str, start: dt.date | None, end: dt.date | None) -> dict: ...


class SQLiteSeriesStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS observation (
                           series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL,
                           source TEXT NOT NULL, entitlement TEXT NOT NULL,
                           PRIMARY KEY (series_id, date))""")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def upsert(self, rows) -> int:
        rows = [(s, d.isoformat(), float(v), src, ent) for s, d, v, src, ent in rows]
        with self._lock, self._conn() as c:
            c.executemany("INSERT INTO observation VALUES (?,?,?,?,?) ON CONFLICT(series_id, date) "
                          "DO UPDATE SET value=excluded.value, source=excluded.source, "
                          "entitlement=excluded.entitlement", rows)
        return len(rows)

    @staticmethod
    def _range(start, end) -> tuple[str, list]:
        sql, args = "", []
        if start:
            sql += " AND date >= ?"
            args.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            args.append(end.isoformat())
        return sql, args

    def observations(self, series_id, start=None, end=None, limit=400):
        where, args = self._range(start, end)
        with self._conn() as c:
            rows = c.execute(f"SELECT date, value FROM observation WHERE series_id = ?{where} "
                             "ORDER BY date DESC LIMIT ?", [series_id, *args, int(limit)]).fetchall()
        return list(reversed(rows))

    def summary(self, series_id, start=None, end=None) -> dict:
        where, args = self._range(start, end)
        with self._conn() as c:
            n, lo, hi, avg, first, last = c.execute(
                f"SELECT count(*), min(value), max(value), avg(value), min(date), max(date) FROM observation "
                f"WHERE series_id = ?{where}", [series_id, *args]).fetchone()
        return {"count": n, "min": lo, "max": hi, "mean": round(avg, 4) if avg is not None else None,
                "first_date": first, "last_date": last}


_store: SeriesStore | None = None


def get_store() -> SeriesStore:
    global _store
    if _store is None:
        url = settings.timeseries_url
        if not url.startswith("sqlite:///"):
            raise ValueError("only sqlite:/// is bundled; implement SeriesStore for other backends")
        path = Path(url[len("sqlite:///"):])
        _store = SQLiteSeriesStore(path if path.is_absolute() else ROOT / path)
    return _store
