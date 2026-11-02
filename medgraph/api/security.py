"""Request-level security: authentication + authorisation dependency, rate limits,
client-IP resolution, and ASGI middleware (request id, body limit, headers, access log)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from ipaddress import ip_address, ip_network

from fastapi import Header, HTTPException, Request

from ..config import settings
from ..observability import log, metrics, request_id_var
from ..security.auth import ANONYMOUS_DEV, AuthError, KeyStore, Principal, extract_key
from ..security.ratelimit import RateLimiter

logger = logging.getLogger("medgraph.api")

_keystore: KeyStore | None = None
read_limiter = RateLimiter(settings.rate_limit_read_per_min)
chat_limiter = RateLimiter(settings.rate_limit_chat_per_min, burst=max(1, settings.rate_limit_chat_per_min // 2))
anon_limiter = RateLimiter(settings.rate_limit_anon_per_min)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_TRUSTED = [ip_network(p, strict=False) for p in settings.trusted_proxies]


def keystore() -> KeyStore:
    global _keystore
    if _keystore is None:
        _keystore = KeyStore()
    return _keystore


def client_ip(request: Request) -> str:
    """Peer address; X-Forwarded-For is honoured only from configured trusted proxies."""
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = any(ip_address(peer) in net for net in _TRUSTED)
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer


def _limit(limiter: RateLimiter, key: str, scope: str) -> None:
    allowed, retry = limiter.check(key)
    if not allowed:
        metrics.inc("medgraph_rate_limited_total", scope=scope)
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(max(1, round(retry)))})


def require(scope: str):
    """FastAPI dependency: authenticate, authorise `scope`, then apply the rate limit."""

    def dependency(request: Request, authorization: str | None = Header(None),
                   x_api_key: str | None = Header(None)) -> Principal:
        ip = client_ip(request)
        if settings.auth_disabled and not settings.is_production:
            principal = ANONYMOUS_DEV
            _limit(anon_limiter, f"ip:{ip}", scope)
        else:
            try:
                principal = keystore().authenticate(extract_key(authorization, x_api_key))
            except AuthError as exc:
                _limit(anon_limiter, f"authfail:{ip}", "auth")  # slows key guessing
                metrics.inc("medgraph_auth_failures_total")
                raise HTTPException(exc.status, exc.message, headers={"WWW-Authenticate": "Bearer"}) from None
            if not principal.can(scope):
                metrics.inc("medgraph_forbidden_total", scope=scope)
                raise HTTPException(403, f"API key lacks the '{scope}' scope")
        _limit(chat_limiter if scope == "chat" else read_limiter, f"{principal.name}:{ip}", scope)
        request.state.principal = principal.name
        return principal

    return dependency


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
     b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


class RequestContextMiddleware:
    """Request id, body-size limit, security headers, access log and request metrics (pure ASGI)."""

    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_json_error(status: int, message: str):
            body = (f'{{"error":{{"status":{status},"message":"{message}","request_id":"{rid}"}}}}').encode()
            await send_wrapper({"type": "http.response.start", "status": status,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
            await send_wrapper({"type": "http.response.body", "body": body})

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs = list(message.get("headers", []))
                existing = {k.lower() for k, _ in hdrs}
                hdrs += [(k, v) for k, v in SECURITY_HEADERS if k not in existing]
                hdrs.append((b"x-request-id", rid.encode()))
                if settings.is_production:
                    hdrs.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message = {**message, "headers": hdrs}
            await send(message)

        try:
            declared = headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_body:
                return await send_json_error(413, "request body too large")

            received = 0

            async def limited_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_body:
                        raise _BodyTooLarge()
                return message

            try:
                await self.app(scope, limited_receive, send_wrapper)
            except _BodyTooLarge:
                await send_json_error(413, "request body too large")
        finally:
            ms = round((time.perf_counter() - start) * 1000)
            route = scope.get("route")
            # Route templates (not raw paths) keep metric/label cardinality bounded.
            path = getattr(route, "path", None) or ("/static" if scope.get("path", "").startswith("/static/")
                                                    else "unmatched")
            metrics.inc("medgraph_http_requests_total", method=scope["method"], route=path,
                        status=status_holder["status"])
            metrics.inc("medgraph_http_request_ms_sum", ms, route=path)
            log(logger, logging.INFO, "request", method=scope["method"], route=path,
                status=status_holder["status"], ms=ms)
            request_id_var.reset(token)


class _BodyTooLarge(Exception):
    pass
