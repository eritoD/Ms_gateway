from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.test_users_proxy import SECRET, token_headers


@contextmanager
def matching_client(monkeypatch, handler):
    monkeypatch.setenv("MATCHING_SERVICE_URL", "http://ms_matching:8002")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    app = create_app()
    try:
        with TestClient(app) as client:
            app.state.matching_client._transport = httpx.MockTransport(handler)
            yield client
    finally:
        get_settings.cache_clear()


def test_matching_requires_auth_and_preserves_request_and_response(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "pending"})
    with matching_client(monkeypatch, upstream) as client:
        assert client.get("/api/v1/matching/state").status_code == 401
        assert calls == []
        response = client.post("/api/v1/matching/requests", headers={**token_headers(), "X-User-ID": "forged"}, json={"recipient_id": "recipient"})
        assert response.json() == {"status": "pending"}
        assert response.headers["cache-control"] == "no-store"
    assert calls[0].url.scheme == "http"
    assert calls[0].url.host == "ms_matching"
    assert calls[0].url.port == 8002
    assert calls[0].url.path == "/api/v1/matching/requests"
    assert calls[0].url.query == b""
    assert "x-user-id" not in calls[0].headers
    assert calls[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.parametrize("status", [403, 404, 409, 422])
def test_preserves_matching_authorization_and_conflict_errors(monkeypatch, status):
    with matching_client(monkeypatch, lambda _: httpx.Response(status, json={"detail": "Denied"})) as client:
        response = client.get("/api/v1/matching/matches/unknown/messages?after_id=5", headers=token_headers())
    assert response.status_code == status
    assert response.json() == {"detail": "Denied"}


def test_unavailable_matching_service_returns_503(monkeypatch):
    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)
    with matching_client(monkeypatch, unavailable) as client:
        assert client.get("/api/v1/matching/state", headers=token_headers()).status_code == 503


def test_cancel_proxies_delete_authentication_and_empty_response(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(204)
    with matching_client(monkeypatch, upstream) as client:
        path = "/api/v1/matching/requests/10000000-0000-4000-8000-000000000002"
        assert client.delete(path).status_code == 401
        assert calls == []
        response = client.delete(path, headers=token_headers())
        assert response.status_code == 204
        assert response.content == b""
    assert calls[0].method == "DELETE"
    assert calls[0].url.path == path
    assert calls[0].headers["authorization"].startswith("Bearer ")
