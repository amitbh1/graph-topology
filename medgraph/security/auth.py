"""API-key authentication with scopes.

Keys are configured as `name:scope1|scope2:sha256hex` in MEDGRAPH_API_KEYS — only
the SHA-256 hash of a key is stored. Create one with `python -m medgraph apikey <name>`.

Scopes:
    read   graph query endpoints
    chat   the LLM assistant (costly — separately rate limited)
    admin  operational endpoints (stats, metrics, ontology)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from ..config import settings

SCOPES = ("read", "chat", "admin")
KEY_PREFIX = "mgk_"


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes and scope in SCOPES


ANONYMOUS_DEV = Principal("anonymous-dev", frozenset(SCOPES))


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def parse_key_entries(entries: tuple[str, ...] | list[str]) -> list[tuple[Principal, str]]:
    parsed = []
    for entry in entries:
        parts = entry.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid MEDGRAPH_API_KEYS entry (expected name:scopes:sha256): {entry[:20]}...")
        name, scopes, digest = parts
        scope_set = frozenset(s for s in scopes.split("|") if s)
        unknown = scope_set - set(SCOPES)
        if unknown or not scope_set:
            raise ValueError(f"API key {name!r} has invalid scopes {sorted(unknown) or '[]'}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(f"API key {name!r} must use a 64-char sha256 hex digest")
        parsed.append((Principal(name, scope_set), digest.lower()))
    return parsed


class KeyStore:
    def __init__(self, entries: tuple[str, ...] | list[str] | None = None):
        self._keys = parse_key_entries(settings.api_keys if entries is None else entries)

    def __len__(self) -> int:
        return len(self._keys)

    def authenticate(self, presented: str | None) -> Principal:
        if not presented:
            raise AuthError(401, "missing API key")
        digest = hash_key(presented.strip())
        match = None
        for principal, expected in self._keys:  # compare against every key: constant-time per key
            if hmac.compare_digest(digest, expected):
                match = principal
        if match is None:
            raise AuthError(401, "invalid API key")
        return match


def extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None
