from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.test_users_proxy import SECRET, token_headers


@contextmanager
def activities_client(monkeypatch, handler):
    monkeypatch.setenv("ACTIVITIES_SERVICE_URL", "http://ms_activities:8003")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    app = create_app()
    try:
        with TestClient(app) as client:
            app.state.activities_client._transport = httpx.MockTransport(handler)
            yield client
    finally:
        get_settings.cache_clear()



def test_create_list_and_detail_preserve_identity_query_and_status(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(201 if request.method == "POST" else 200, json={"id": "activity"})
    with activities_client(monkeypatch, upstream) as client:
        assert client.get("/api/v1/activities").status_code == 401
        assert client.post("/api/v1/activities", json={}).status_code == 401
        assert calls == []
        body = {"title": "Running", "location": "Parque"}
        response = client.post("/api/v1/activities", headers={**token_headers(), "X-User-ID": "forged"}, json=body)
        assert response.status_code == 201 and response.json()['id'] == 'activity'
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/api/v1/activities?limit=3&cursor=abc', headers=token_headers()).status_code == 200
        assert client.get('/api/v1/activities/abc', headers=token_headers()).status_code == 200
    assert calls[0].url.host == 'ms_activities' and calls[0].url.port == 8003
    assert 'x-user-id' not in calls[0].headers
    assert calls[0].headers['authorization'].startswith('Bearer ')
    assert calls[1].url.query == b'limit=3&cursor=abc'
    assert calls[2].url.path == '/api/v1/activities/abc'


@pytest.mark.parametrize('status', [401, 403, 404, 409, 422, 503])
def test_activities_errors_preserved(monkeypatch, status):
    with activities_client(monkeypatch, lambda _: httpx.Response(status, json={'detail': 'Denied'})) as client:
        response = client.get('/api/v1/activities', headers=token_headers())
        assert response.status_code == status and response.json() == {'detail': 'Denied'}


def test_offline_activities_return_503_and_readiness_fails(monkeypatch):
    def offline(request):
        raise httpx.ConnectError('offline', request=request)
    with activities_client(monkeypatch, offline) as client:
        assert client.get('/api/v1/activities', headers=token_headers()).status_code == 503
        assert client.get('/health/ready').status_code == 503


def test_applications_require_token_and_forward_path(monkeypatch):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(201 if request.method == 'POST' else 200, json={'status': 'pending'})
    with activities_client(monkeypatch, upstream) as client:
        base = '/api/v1/activities/abc/applications'
        assert client.post(base).status_code == 401
        assert client.get(base + '/me').status_code == 401
        assert calls == []
        response = client.post(base, headers={**token_headers(), 'X-User-ID': 'forged'})
        assert response.status_code == 201 and response.json() == {'status': 'pending'}
        assert client.get(base + '/me', headers=token_headers()).status_code == 200
        assert client.get(base, headers=token_headers()).status_code == 200
    assert [(c.method, c.url.path) for c in calls] == [
        ('POST', base), ('GET', base + '/me'), ('GET', base)]
    assert 'x-user-id' not in calls[0].headers
    assert calls[0].headers['authorization'].startswith('Bearer ')


@pytest.mark.parametrize('action', ['accept', 'reject'])
def test_decisions_require_token_and_forward_path(monkeypatch, action):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'status': action + 'ed'})
    with activities_client(monkeypatch, upstream) as client:
        path = f'/api/v1/activities/abc/applications/def/{action}'
        assert client.post(path).status_code == 401
        assert calls == []
        response = client.post(path, headers={**token_headers(), 'X-User-ID': 'forged'})
        assert response.status_code == 200 and response.json() == {'status': action + 'ed'}
        assert client.get(path, headers=token_headers()).status_code == 405
    assert len(calls) == 1 and calls[0].method == 'POST' and calls[0].url.path == path
    assert 'x-user-id' not in calls[0].headers
