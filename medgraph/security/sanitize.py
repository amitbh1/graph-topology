"""Input sanitisation shared by the API and the assistant tools.

Every user-supplied string goes through `clean_text` (Unicode normalisation,
control / zero-width / bidi characters removed, whitespace collapsed, length
cap) and entity arguments additionally through `clean_entity` (allow-list of
characters that occur in drug, disease and code names).
"""

from __future__ import annotations

import re
import unicodedata

# Zero-width and bidirectional control characters are used to hide instructions.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ENTITY_OK = re.compile(r"[^\w\s\-+,.'()/&%]", re.UNICODE)

ENTITY_MAX_CHARS = 120
ATC_CODE = re.compile(r"^[A-Z](\d{2}([A-Z]([A-Z](\d{2})?)?)?)?$")
TRIAL_STATUSES = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED", "NOT_YET_RECRUITING",
                  "ENROLLING_BY_INVITATION", "SUSPENDED", "TERMINATED", "WITHDRAWN", "UNKNOWN")


class InputError(ValueError):
    """User input rejected by validation (maps to HTTP 422 / a tool error)."""


def clean_text(value: str | None, max_chars: int, *, field: str = "input", required: bool = True) -> str:
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


def clean_entity(value: str | None, *, field: str = "name", max_chars: int = ENTITY_MAX_CHARS) -> str:
    """A drug / disease / term argument. Small models sometimes pass 'drug: Glucotrol' or quotes."""
    text = clean_text(value, max_chars * 2, field=field)
    text = text.strip("'\"` ")
    head, sep, tail = text.partition(":")
    if sep and len(head) < 15 and tail.strip():
        text = tail.strip()
    text = _ENTITY_OK.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise InputError(f"{field} has no usable characters")
    if len(text) > max_chars:
        raise InputError(f"{field} is longer than {max_chars} characters")
    return text


def clean_status(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    status = str(value).strip().upper().replace(" ", "_")
    if status not in TRIAL_STATUSES:
        raise InputError(f"status must be one of {', '.join(TRIAL_STATUSES)}")
    return status


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))
