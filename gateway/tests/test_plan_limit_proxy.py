"""HU-54: el 402 LIMITE_PLAN de los servicios llega tal cual (estado y cuerpo) a la app y a la web."""
import httpx
import pytest

from tests.test_activities_proxy import activities_client
from tests.test_clubs_proxy import ID, gateway
from tests.test_matching_proxy import matching_client
from tests.test_users_proxy import token_headers

LIMIT = {"codigo": "LIMITE_PLAN", "recurso": "comunicados", "limite": 4, "usado": 4,
         "se_libera_en": "2026-11-09T12:00:00Z", "detail": "Te quedan 0 de 4 comunicados cada 24 h en el plan gratuito."}


def limited(_request):
    return httpx.Response(402, json=LIMIT, headers={"Cache-Control": "no-store"})


def test_matching_request_limit_is_relayed(monkeypatch):
    with matching_client(monkeypatch, limited) as client:
        response = client.post("/api/v1/matching/requests", headers=token_headers(), json={"recipient_id": ID})
    assert response.status_code == 402
    assert response.json() == LIMIT


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/api/v1/activities"),
    ("POST", f"/api/v1/activities/{ID}/applications"),
])
def test_activities_limits_are_relayed(monkeypatch, method, path):
    with activities_client(monkeypatch, limited) as client:
        response = client.request(method, path, headers=token_headers(), json={})
    assert response.status_code == 402
    assert response.json() == LIMIT


@pytest.mark.parametrize("path", [
    f"/api/v1/clubs/{ID}/announcements",
    f"/api/v1/clubs/{ID}/notices",
    f"/api/v1/clubs/{ID}/canales",
    f"/api/v1/clubs/announcements/{ID}/applications",
])
def test_club_limits_are_relayed(monkeypatch, path):
    with gateway(monkeypatch, "clubs", limited) as client:
        response = client.post(path, headers=token_headers(), json={})
    assert response.status_code == 402
    assert response.json() == LIMIT
    assert response.headers["cache-control"] == "no-store"


USAGE = {"plan": "GRATIS", "items": [{"recurso": "solicitudes_match", "limite": 6, "usado": 2,
                                      "ventana_horas": 24, "se_libera_en": None}]}


@pytest.mark.parametrize(("service", "path"), [
    ("matching", "/api/v1/matching/plan-usage"),
    ("activities", "/api/v1/activities/plan-usage"),
    ("clubs", "/api/v1/clubs/plan-usage/me"),
])
def test_plan_usage_needs_a_token_and_is_forwarded_as_is(monkeypatch, service, path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=USAGE)
    opener = {"matching": matching_client, "activities": activities_client}.get(service)
    context = opener(monkeypatch, upstream) if opener else gateway(monkeypatch, service, upstream)
    with context as client:
        assert client.get(path).status_code == 401 and calls == []
        response = client.get(path, headers=token_headers())
    assert response.status_code == 200 and response.json() == USAGE
    assert calls[0].url.path == path and calls[0].headers["authorization"].startswith("Bearer ")
    assert response.headers["cache-control"] == "no-store"
