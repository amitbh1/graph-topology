"""Structured JSON logging, request ids and in-process metrics."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import sys
import threading
import time
from collections import defaultdict

from .config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"fields": fields})


def fingerprint(text: str) -> str:
    """Stable, non-reversible reference to user text (for audit logs without health data)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Metrics:
    """Minimal counters + latency sums, exposed in Prometheus text format."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def render(self) -> str:
        lines = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lbl = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"{name}{{{lbl}}} {value}" if lbl else f"{name} {value}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
