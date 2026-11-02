"""Input sanitisation shared by the API and the assistant tools.

`clean_text`: Unicode NFKC, zero-width / bidi / control characters removed,
whitespace collapsed, length cap. `clean_entity`: plus an allow-list of
characters that occur in asset, company, commodity and region names.
Enumerations and dates are validated against allow-lists / ISO formats.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from ..sources.records import ASSET_TYPES

_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ENTITY_OK = re.compile(r"[^\w\s\-+,.'()/&%:]", re.UNICODE)

ENTITY_MAX_CHARS = 120
PROJECT_STATUSES = ("announced", "permitting", "fid", "construction", "commissioning", "operating", "suspended",
                    "cancelled")
INCIDENT_TYPES = ("planned_outage", "unplanned_outage", "forced_outage", "safety", "environmental", "other")
MIN_DATE, MAX_DATE = dt.date(1900, 1, 1), dt.date(2100, 12, 31)


class InputError(ValueError):
    """User input rejected by validation (HTTP 422 / a tool error)."""


def clean_text(value, max_chars: int, *, field: str = "input", required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InputError(f"{field} must be a string")
    text = unicodedata.normalize("NFKC", value)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text).strip()
    if required and not text:
        raise InputError(f"{field} must not be empty")
    if len(text) > max_chars:
        raise InputError(f"{field} is longer than {max_chars} characters")
    return text


def clean_entity(value, *, field: str = "name", max_chars: int = ENTITY_MAX_CHARS) -> str:
    """Asset / company / term argument. Strips wrappers such as 'asset: Sabine Pass' and quotes."""
    text = clean_text(value, max_chars * 2, field=field).strip("'\"` ")
    head, sep, tail = text.partition(":")
    if sep and head.lower() in ("asset", "company", "drug", "term", "name", "region", "commodity", "plant") \
            and tail.strip():
        text = tail.strip()
    text = re.sub(r"\s+", " ", _ENTITY_OK.sub(" ", text)).strip()
    if not text:
        raise InputError(f"{field} has no usable characters")
    if len(text) > max_chars:
        raise InputError(f"{field} is longer than {max_chars} characters")
    return text


def _choice(value, allowed: tuple[str, ...], field: str, transform=str.lower) -> str | None:
    if value is None or not str(value).strip():
        return None
    v = transform(str(value).strip().replace(" ", "_"))
    if v not in allowed:
        raise InputError(f"{field} must be one of {', '.join(allowed)}")
    return v


def clean_project_status(value) -> str | None:
    return _choice(value, PROJECT_STATUSES, "status")


def clean_incident_type(value) -> str | None:
    return _choice(value, INCIDENT_TYPES, "incident_type")


def clean_asset_type(value) -> str | None:
    if value is None or not str(value).strip():
        return None
    lookup = {t.lower(): t for t in ASSET_TYPES}
    v = lookup.get(str(value).strip().replace(" ", "").replace("_", "").lower())
    if not v:
        raise InputError(f"asset_type must be one of {', '.join(ASSET_TYPES)}")
    return v


def clean_country(value) -> str | None:
    if value is None or not str(value).strip():
        return None
    v = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", v):
        raise InputError("country must be an ISO 3166 alpha-3 code, e.g. USA")
    return v


def clean_date(value, field: str = "date") -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.date):
        d = value
    else:
        try:
            d = dt.date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise InputError(f"{field} must be an ISO date (YYYY-MM-DD)") from None
    if not MIN_DATE <= d <= MAX_DATE:
        raise InputError(f"{field} is out of range")
    return d


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))
