from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET = "integration-test-secret-with-more-than-thirty-two-bytes"
USER_ID = str(uuid4())


def token_headers():
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "sub": USER_ID, "iss": "sportmatch-auth", "aud": "sportmatch-mobile",
        "iat": now, "exp": now + timedelta(minutes=10), "token_type": "access",
        "roles": ["player"],
    }, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def proxy_client(monkeypatch, handler):
    monkeypatch.setenv("USERS_SERVICE_URL", "http://ms_users:8001")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    application = create_app()
    try:
        with TestClient(application) as client:
            # Keep the real lifespan/connection cleanup, replace only transport.
            application.state.users_client._transport = httpx.MockTransport(handler)
            yield client
    finally:
        get_settings.cache_clear()


def test_register_forwards_body_query_and_status_without_auth(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(201, json={"access_token": "opaque", "user": {"user_id": USER_ID}})
    with proxy_client(monkeypatch, upstream) as client:
        response = client.post("/api/v1/users/auth/register?source=web&source=app", content=b'{"nombre":"Ana"}', headers={"Content-Type": "application/json", "X-User-ID": "fake-admin", "X-Forwarded-Host": "evil.example", "X-Request-ID": "request-42"})
    assert response.status_code == 201
    assert str(calls[0].url) == "http://ms_users:8001/api/v1/users/auth/register?source=web&source=app"
    assert calls[0].content == b'{"nombre":"Ana"}'
    assert calls[0].headers["x-request-id"] == "request-42"
    assert "x-user-id" not in calls[0].headers
    assert "x-forwarded-host" not in calls[0].headers
    assert response.headers["cache-control"] == "no-store"


def test_private_route_requires_valid_token_before_forwarding(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"user_id": USER_ID})
    with proxy_client(monkeypatch, upstream) as client:
        path = f"/api/v1/users/{USER_ID}/profile"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert calls == []
        headers = token_headers()
        assert client.get(path, headers=headers).status_code == 200
        assert calls[0].headers["authorization"] == headers["Authorization"]


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422])
def test_upstream_errors_are_preserved(monkeypatch, status):
    def upstream(request):
        return httpx.Response(status, json={"detail": "upstream error"}, headers={"WWW-Authenticate": "Bearer"})
    with proxy_client(monkeypatch, upstream) as client:
        response = client.post("/api/v1/users/auth/login", json={})
    assert response.status_code == status
    assert response.json() == {"detail": "upstream error"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("error,status", [(httpx.ConnectError, 503), (httpx.ReadTimeout, 504)])
def test_unavailable_upstream_has_controlled_error(monkeypatch, error, status):
    def upstream(request):
        raise error("internal connection detail", request=request)
    with proxy_client(monkeypatch, upstream) as client:
        response = client.post("/api/v1/users/auth/login", json={})
    assert response.status_code == status
    assert "internal connection detail" not in response.text


def test_delete_preserves_empty_204(monkeypatch):
    with proxy_client(monkeypatch, lambda request: httpx.Response(204)) as client:
        response = client.delete(f"/api/v1/users/{USER_ID}", headers=token_headers())
    assert response.status_code == 204
    assert response.content == b""


def test_readiness_checks_users_database_and_unknown_routes_are_not_proxied(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(503, json={"detail": "database unavailable"})
    with proxy_client(monkeypatch, upstream) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/users/admin/arbitrary").status_code == 404
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/users/health/ready"


def test_openapi_exposes_path_parameters_and_public_login(monkeypatch):
    with proxy_client(monkeypatch, lambda request: httpx.Response(200)) as client:
        paths = client.get("/openapi.json").json()["paths"]
    profile = paths["/api/v1/users/{user_id}/profile"]["get"]
    assert profile["parameters"][0]["name"] == "user_id"
    assert profile["parameters"][0]["required"] is True
    assert profile["security"] == [{"JWTBearer": []}]
    login = paths["/api/v1/users/auth/login"]["post"]
    assert not login.get("security")
    assert "email" in login["requestBody"]["content"]["application/json"]["example"]
