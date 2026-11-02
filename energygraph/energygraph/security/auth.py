"""Authentication: API keys (service accounts) and OIDC JWT bearer tokens (SSO users).

A Principal carries
    scopes        what the caller may do:   read | chat | admin
    entitlements  what data they may see:   public, org:<dataset>, or * (all)

API keys are configured as `name:scopes:entitlements:sha256hex`, e.g.
    analyst:read|chat:public|org:power-assets|org:outages:<sha256>
Only the SHA-256 hash is stored. Create keys with `python -m energygraph apikey`.

OIDC tokens are verified against the issuer's JWKS (signature, exp, nbf, iss,
aud); scopes come from OIDC_SCOPE_CLAIM and entitlements from
OIDC_ENTITLEMENT_CLAIM. `public` is always granted to authenticated callers.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from ..config import settings

SCOPES = ("read", "chat", "admin")
KEY_PREFIX = "egk_"
ENTITLEMENT_RE = re.compile(r"^(public|\*|org:[a-z0-9-]{1,40})$")


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]
    entitlements: frozenset[str]
    kind: str = "api_key"   # api_key | oidc | dev

    def can(self, scope: str) -> bool:
        return scope in self.scopes or ("admin" in self.scopes and scope in SCOPES)

    @property
    def all_data(self) -> bool:
        return "*" in self.entitlements

    def ents(self) -> list[str]:
        """Entitlement list passed to every query as $ents."""
        return sorted(self.entitlements | {"public"})

    def sees(self, entitlement: str | None) -> bool:
        return self.all_data or (entitlement or "") in self.ents()


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def _entitlements(values) -> frozenset[str]:
    ents = frozenset(v for v in values if v)
    bad = [e for e in ents if not ENTITLEMENT_RE.match(e)]
    if bad:
        raise ValueError(f"invalid entitlements {bad}")
    return ents | {"public"}


def parse_key_entries(entries) -> list[tuple[Principal, str]]:
    parsed = []
    for entry in entries:
        parts = entry.split(":")
        # entitlements may themselves contain ':' (org:<dataset>) -> name, scopes, ..., digest
        if len(parts) < 4:
            raise ValueError(f"invalid ENERGYGRAPH_API_KEYS entry (name:scopes:entitlements:sha256): {entry[:20]}...")
        name, scopes, digest = parts[0], parts[1], parts[-1]
        ent_text = ":".join(parts[2:-1])
        scope_set = frozenset(s for s in scopes.split("|") if s)
        if not scope_set or scope_set - set(SCOPES):
            raise ValueError(f"API key {name!r} has invalid scopes")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(f"API key {name!r} must use a 64-char sha256 hex digest")
        parsed.append((Principal(name, scope_set, _entitlements(ent_text.split("|"))), digest.lower()))
    return parsed


class KeyStore:
    def __init__(self, entries=None):
        self._keys = parse_key_entries(settings.api_keys if entries is None else entries)

    def __len__(self) -> int:
        return len(self._keys)

    def authenticate(self, presented: str) -> Principal:
        digest = hash_key(presented.strip())
        match = None
        for principal, expected in self._keys:  # constant-time comparison against every key
            if hmac.compare_digest(digest, expected):
                match = principal
        if match is None:
            raise AuthError(401, "invalid credentials")
        return match


class OIDCVerifier:
    ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]

    def __init__(self, issuer: str, audience: str, jwks_url: str, jwk_client=None):
        import jwt

        self._jwt = jwt
        self.issuer, self.audience = issuer, audience
        self.jwks = jwk_client or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)

    def authenticate(self, token: str) -> Principal:
        jwt = self._jwt
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=self.ALGORITHMS, audience=self.audience, issuer=self.issuer,
                                leeway=30, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWTError:
            raise AuthError(401, "invalid credentials") from None
        raw_scopes = claims.get(settings.oidc_scope_claim, "")
        scopes = raw_scopes.split() if isinstance(raw_scopes, str) else list(raw_scopes or [])
        scopes = frozenset(s.split(":", 1)[-1] if s.startswith("energygraph:") else s for s in scopes) & set(SCOPES)
        if not scopes:
            raise AuthError(403, "token grants no EnergyGraph scope")
        raw_ents = claims.get(settings.oidc_entitlement_claim, [])
        ents = raw_ents.split() if isinstance(raw_ents, str) else list(raw_ents or [])
        try:
            entitlements = _entitlements(ents)
        except ValueError:
            raise AuthError(403, "token carries invalid entitlements") from None
        return Principal(f"oidc:{str(claims['sub'])[:64]}", scopes, entitlements, kind="oidc")


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and not token.startswith(KEY_PREFIX)


def extract_credential(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
