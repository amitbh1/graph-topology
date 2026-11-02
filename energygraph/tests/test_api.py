"""HTTP security: authentication, scopes, entitlements, validation, limits, headers."""

import importlib

import pytest
from fastapi.testclient import TestClient

from energygraph.security.auth import generate_key, hash_key

PUBLIC_KEY, LICENSED_KEY, ADMIN_KEY = generate_key(), generate_key(), generate_key()


@pytest.fixture()
def api(monkeypatch, db):
    monkeypatch.setenv("ENERGYGRAPH_API_KEYS", ",".join([
        f"reader:read:public:{hash_key(PUBLIC_KEY)}",
        f"analyst:read|chat:public|org:power-assets|org:outages:{hash_key(LICENSED_KEY)}",
        f"ops:admin:public:{hash_key(ADMIN_KEY)}"]))
    monkeypatch.setenv("ENERGYGRAPH_AUTH_DISABLED", "false")
    monkeypatch.setenv("MAX_BODY_BYTES", "2048")
    import energygraph.api.main as main
    import energygraph.api.security as security
    import energygraph.config as config
    import energygraph.security.auth as auth

    # Reload only modules that read settings at import; never reload modules whose classes other tests hold.
    importlib.reload(config)
    monkeypatch.setattr(auth, "settings", config.settings)
    for m in (security, main):
        importlib.reload(m)
    with TestClient(main.app) as client:
        yield client, security
    monkeypatch.undo()
    for m in (config, security, main):
        importlib.reload(m)


def H(key):
    return {"X-API-Key": key}


def test_authentication(api):
    c, _ = api
    assert c.get("/api/sources").status_code == 401
    assert c.get("/api/sources", headers=H("egk_nope")).status_code == 401
    assert c.get("/api/sources", headers={"Authorization": "Bearer a.b.c"}).status_code == 401  # OIDC off
    assert c.get("/api/me", headers={"Authorization": f"Bearer {PUBLIC_KEY}"}).json()["entitlements"] == ["public"]


def test_scopes(api):
    c, _ = api
    assert c.get("/api/stats", headers=H(PUBLIC_KEY)).status_code == 403
    assert c.get("/metrics", headers=H(ADMIN_KEY)).status_code == 200
    assert c.post("/api/chat", json={"question": "hi"}, headers=H(PUBLIC_KEY)).status_code == 403


def test_entitlements_over_http(api):
    c, _ = api
    assert c.get("/api/asset/Kestrel Bay Power Station", headers=H(PUBLIC_KEY)).json()["found"] is False
    assert c.get("/api/asset/Kestrel Bay Power Station", headers=H(LICENSED_KEY)).json()["asset"]["capacity"] == 780.0
    srcs = {s["source"]: s for s in c.get("/api/sources", headers=H(LICENSED_KEY)).json()["sources"]}
    assert srcs["org-power-assets"]["entitled"] and not srcs["org-news"]["entitled"]


def test_validation_without_echo(api):
    c, _ = api
    h = H(PUBLIC_KEY)
    r = c.get("/api/search", params={"term": "z" * 400}, headers=h)
    assert r.status_code == 422 and "z" * 50 not in r.text
    assert c.get("/api/projects", params={"country": "United States"}, headers=h).status_code == 422
    assert c.get("/api/projects", params={"status": "DROP TABLE"}, headers=h).status_code == 422
    assert c.get("/api/prices", params={"series": "x", "start": "2025-99-01"}, headers=h).status_code == 422
    assert c.get("/api/convert", params={"value": 1, "from_unit": "bbl", "to_unit": "MWh"}, headers=h).status_code == 422
    assert c.get("/api/convert", params={"value": 1, "from_unit": "bbl", "to_unit": "m3"}, headers=h).json()["result"] == 0.158987


def test_chat_payload_limits(api):
    c, _ = api
    h = H(LICENSED_KEY)
    assert c.post("/api/chat", json={"question": "hi", "role": "admin"}, headers=h).status_code == 422
    assert c.post("/api/chat", json={"question": "hi", "history": [{"role": "system", "content": "x"}]},
                  headers=h).status_code == 422
    big = {"question": "y" * 900, "history": [{"role": "user", "content": "z" * 3000}]}
    assert c.post("/api/chat", json=big, headers=h).status_code == 413


def test_headers_and_request_id(api):
    c, _ = api
    r = c.get("/healthz", headers={"X-Request-ID": "abcdef123456"})
    assert r.headers["x-request-id"] == "abcdef123456"
    assert "script-src 'self';" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert c.get("/healthz", headers={"X-Request-ID": "<x>"}).headers["x-request-id"] != "<x>"


def test_rate_limit(api, monkeypatch):
    c, security = api
    from energygraph.security.ratelimit import RateLimiter

    monkeypatch.setattr(security, "read_limiter", RateLimiter(per_minute=2))
    codes = [c.get("/api/me", headers=H(PUBLIC_KEY)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_production_refuses_unsafe_config(monkeypatch):
    monkeypatch.setenv("ENERGYGRAPH_ENV", "production")
    monkeypatch.setenv("ENERGYGRAPH_AUTH_DISABLED", "true")
    monkeypatch.setenv("ORG_SOURCE_MODE", "http")
    monkeypatch.setenv("ORG_SOURCE_BASE_URL", "http://insecure.example")
    monkeypatch.delenv("ENERGYGRAPH_API_KEYS", raising=False)
    import energygraph.config as config

    importlib.reload(config)
    try:
        problems = " ".join(config.settings.problems())
        for expected in ("AUTH_DISABLED", "ENERGYGRAPH_API_KEYS", "NEO4J_PASSWORD", "https://"):
            assert expected in problems
    finally:
        monkeypatch.undo()
        importlib.reload(config)
