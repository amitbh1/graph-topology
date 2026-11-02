"""In-process token-bucket rate limiter and concurrency gate.

Per-process state: behind several API replicas, put the limits in a shared
store (e.g. Redis) or at the gateway; the interface stays the same.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, per_minute: int, burst: int | None = None, max_keys: int = 50_000):
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                if len(self._buckets) >= self.max_keys:
                    self._evict(now)
                b = self._buckets[key] = _Bucket(self.capacity, now)
            b.tokens = min(self.capacity, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True, 0.0
            return False, (cost - b.tokens) / self.rate if self.rate else 60.0

    def _evict(self, now: float) -> None:
        full = [k for k, b in self._buckets.items()
                if b.tokens + (now - b.updated) * self.rate >= self.capacity]
        for k in full or list(self._buckets)[: len(self._buckets) // 2]:
            self._buckets.pop(k, None)


class ConcurrencyGate:
    """Caps simultaneous agent runs; extra requests fail fast instead of queueing."""

    def __init__(self, limit: int):
        self._sem = threading.BoundedSemaphore(limit)

    def __enter__(self):
        if not self._sem.acquire(blocking=False):
            raise BusyError()
        return self

    def __exit__(self, *exc):
        self._sem.release()


class BusyError(Exception):
    pass
