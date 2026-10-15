"""ms-club, the chat list and the new Users routes through the gateway."""
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.test_users_proxy import SECRET, token_headers

ID = str(uuid4())
OTHER = str(uuid4())


@contextmanager
def gateway(monkeypatch, service, handler):
    """The gateway with one upstream replaced by `handler`. service: clubs, matching or users."""
    env = {"clubs": "CLUBS_SERVICE_URL", "matching": "MATCHING_SERVICE_URL", "users": "USERS_SERVICE_URL"}
    for name in env.values():
        monkeypatch.delenv(name, raising=False)
    if handler is not None:
        monkeypatch.setenv(env[service], {"clubs": "http://ms_club:8004", "matching": "http://ms_matching:8002",
                                          "users": "http://ms_users:8001"}[service])
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    application = create_app()
    try:
        with TestClient(application, follow_redirects=False) as client:
            if handler is not None:
                getattr(application.state, f"{service}_client")._transport = httpx.MockTransport(handler)
            yield client
    finally:
        get_settings.cache_clear()


class Upstream:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or httpx.Response(200, json={"ok": True})

    def __call__(self, request):
        self.calls.append(request)
        return self.response


# Every route of ms-club the gateway exposes, with whether a visitor may call it.
PUBLIC = [
    ("GET", "/api/v1/clubs?nombre=%C3%91u%C3%B1oa&page=2"),
    ("GET", "/api/v1/clubs/deportes"),
    ("GET", "/api/v1/clubs/announcements?estado=ABIERTA&edad=16"),
    ("GET", f"/api/v1/clubs/announcements/{ID}"),
    ("GET", f"/api/v1/clubs/{ID}"),
    ("GET", "/api/v1/payments/plans?tipo=CLUB"),
]
PRIVATE = [
    ("POST", "/api/v1/clubs"),
    ("GET", "/api/v1/clubs/me"),
    ("PATCH", f"/api/v1/clubs/{ID}"),
    ("PUT", f"/api/v1/clubs/{ID}/deportes/2"),
    ("DELETE", f"/api/v1/clubs/{ID}/deportes/2"),
    ("POST", f"/api/v1/clubs/{ID}/announcements"),
    ("POST", f"/api/v1/clubs/{ID}/announcements/{OTHER}/close"),
    ("GET", "/api/v1/clubs/announcements/applications/me?estado=PENDIENTE"),
    ("PATCH", f"/api/v1/clubs/announcements/applications/{OTHER}"),
    ("GET", f"/api/v1/clubs/announcements/{ID}/applications?page_size=10"),
    ("POST", f"/api/v1/clubs/announcements/{ID}/applications"),
    ("GET", "/api/v1/clubs/notices/me?prioridad=URGENTE"),
    ("GET", f"/api/v1/clubs/{ID}/notices"),
    ("POST", f"/api/v1/clubs/{ID}/notices"),
    ("POST", "/api/v1/payments/webpay/initiate"),
    ("POST", "/api/v1/payments/webpay/commit"),
    ("GET", "/api/v1/payments/subscription/me"),
]


@pytest.mark.parametrize(("method", "path"), PUBLIC)
def test_the_public_directory_works_without_a_session(monkeypatch, method, path):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        response = client.request(method, path)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert response.headers["cache-control"] == "no-store"
    [call] = upstream.calls
    assert call.method == method
    assert f"{call.url.path}?{call.url.query.decode()}".rstrip("?") == path.replace("%C3%91u%C3%B1oa", "%C3%91u%C3%B1oa")
    assert call.url.host == "ms_club" and call.url.port == 8004
    assert "authorization" not in call.headers


@pytest.mark.parametrize(("method", "path"), PRIVATE)
def test_everything_that_acts_for_a_person_needs_a_session(monkeypatch, method, path):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        assert client.request(method, path).status_code == 401
        assert client.request(method, path, headers={"Authorization": "Bearer basura"}).status_code == 401
        assert upstream.calls == []  # the request never reached ms-club
        response = client.request(method, path, headers=token_headers(), content=b'{"a": 1}' if method != "GET" else None)
    assert response.status_code == 200
    [call] = upstream.calls
    assert call.url.path == path.split("?")[0]
    assert call.headers["authorization"].startswith("Bearer ")
    if method != "GET":
        assert call.content == b'{"a": 1}'


@pytest.mark.parametrize(
    "path",
    ["/api/v1/clubs/me", "/api/v1/clubs/notices/me", "/api/v1/clubs/announcements/applications/me",
     "/api/v1/payments/subscription/me"],
)
def test_a_public_club_id_route_never_swallows_a_private_fixed_path(monkeypatch, path):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        assert client.get(path).status_code == 401
    assert upstream.calls == []


def test_forged_identity_headers_never_reach_the_service(monkeypatch):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        client.post(f"/api/v1/clubs/{ID}/notices", headers={**token_headers(), "X-User-ID": "admin", "X-Forwarded-Host": "evil.example",
                                                            "X-Request-ID": "r-7", "Content-Type": "application/json"}, content=b"{}")
    [call] = upstream.calls
    assert "x-user-id" not in call.headers and "x-forwarded-host" not in call.headers
    assert call.headers["content-type"] == "application/json" and call.headers["x-request-id"]


@pytest.mark.parametrize("status", [400, 403, 404, 409, 502, 503])
def test_the_status_and_body_of_the_service_are_preserved(monkeypatch, status):
    with gateway(monkeypatch, "clubs", lambda request: httpx.Response(status, json={"detail": "Detalle del club"})) as client:
        response = client.get(f"/api/v1/clubs/{ID}")
    assert response.status_code == status and response.json() == {"detail": "Detalle del club"}


def test_an_unavailable_or_slow_ms_club_is_a_503_or_504(monkeypatch):
    def down(request):
        raise httpx.ConnectError("offline", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with gateway(monkeypatch, "clubs", down) as client:
        response = client.get("/api/v1/clubs")
        assert response.status_code == 503 and response.json() == {"detail": "Ms_Club no está disponible"}
    with gateway(monkeypatch, "clubs", slow) as client:
        assert client.get("/api/v1/clubs").status_code == 504


def test_without_a_configured_ms_club_the_routes_answer_503(monkeypatch):
    with gateway(monkeypatch, "clubs", None) as client:
        for path in ("/api/v1/clubs", "/api/v1/payments/plans"):
            response = client.get(path)
            assert response.status_code == 503 and response.json() == {"detail": "Ms_Club no está configurado"}
        assert client.get("/api/v1/clubs/me", headers=token_headers()).status_code == 503


# -- the browser comes back from Webpay ------------------------------------------------------------------------


def test_the_webpay_return_keeps_its_redirect_to_the_app(monkeypatch):
    """ms-club answers 303 with the app's address; the gateway used to drop the Location header."""
    destination = "sportmatchapp://pago?token_ws=" + "a" * 64
    upstream = Upstream(httpx.Response(303, headers={"location": destination}))
    with gateway(monkeypatch, "clubs", upstream) as client:
        by_get = client.get("/api/v1/payments/webpay/return?token_ws=" + "a" * 64)
        by_post = client.post("/api/v1/payments/webpay/return", data={"token_ws": "a" * 64})
    for response in (by_get, by_post):  # Transbank uses GET, or POST in its integration environment
        assert response.status_code == 303 and response.headers["location"] == destination
        assert response.headers["cache-control"] == "no-store"
    assert upstream.calls[0].url.query == ("token_ws=" + "a" * 64).encode()
    assert upstream.calls[1].method == "POST" and upstream.calls[1].content == b"token_ws=" + b"a" * 64
    assert upstream.calls[1].headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert all("authorization" not in call.headers for call in upstream.calls)  # the browser has no session here


def test_only_redirects_carry_a_location_to_the_client(monkeypatch):
    upstream = Upstream(httpx.Response(200, json={"ok": True}, headers={"location": "https://evil.example"}))
    with gateway(monkeypatch, "clubs", upstream) as client:
        assert "location" not in client.get(f"/api/v1/clubs/{ID}").headers


def test_the_simulated_payment_page_reaches_the_browser(monkeypatch):
    page = httpx.Response(200, html="<h1>Simulación de Webpay Plus</h1>")
    with gateway(monkeypatch, "clubs", Upstream(page)) as client:
        response = client.get("/api/v1/payments/webpay/mock?token_ws=" + "b" * 64)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "Simulación" in response.text


# -- not everything ms-club has is exposed -------------------------------------------------------------------------


def test_the_health_check_of_the_service_is_not_exposed(monkeypatch):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        assert client.get("/api/v1/clubs/health/ready", headers=token_headers()).status_code == 404
    assert upstream.calls == []


def test_openapi_documents_the_club_routes_and_marks_the_private_ones(monkeypatch):
    with gateway(monkeypatch, "clubs", Upstream()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "security" not in paths["/api/v1/clubs"]["get"]
    assert "security" in paths["/api/v1/clubs"]["post"] and "security" in paths["/api/v1/clubs/me"]["get"]
    assert "security" not in paths["/api/v1/payments/plans"]["get"]
    assert "security" in paths["/api/v1/payments/webpay/initiate"]["post"]
    parameters = {p["name"]: p["schema"] for p in paths["/api/v1/clubs/{club_id}/deportes/{deporte_id}"]["put"]["parameters"]}
    assert parameters["club_id"]["format"] == "uuid" and parameters["deporte_id"] == {"type": "integer"}
    assert paths["/api/v1/clubs/{club_id}/notices"]["post"]["requestBody"]["content"]["application/json"]["example"]["audiencia"] == "TODOS"
    assert {"page", "page_size", "nombre", "deporte"} <= {p["name"] for p in paths["/api/v1/clubs"]["get"]["parameters"]}


# -- readiness --------------------------------------------------------------------------------------------------------


def test_the_gateway_is_ready_only_if_a_configured_ms_club_is_ready(monkeypatch):
    with gateway(monkeypatch, "clubs", lambda request: httpx.Response(200, json={"status": "ready", "service": "ms_club"})) as client:
        assert client.get("/health/ready").status_code == 200
    with gateway(monkeypatch, "clubs", lambda request: httpx.Response(503, json={"detail": "clubs database unavailable"})) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503 and "Ms_Club" in response.json()["detail"]
    with gateway(monkeypatch, "clubs", None) as client:  # not configured: the gateway does not wait for it
        assert client.get("/health/ready").status_code == 200


# -- the chat list (served by Ms_Matching) ---------------------------------------------------------------------


CHAT_ROUTES = [
    ("GET", "/api/v1/chats?limit=20&offset=0"),
    ("GET", f"/api/v1/chats/{ID}/messages?after_id=5&limit=50"),
    ("POST", f"/api/v1/chats/{ID}/messages"),
    ("PATCH", f"/api/v1/chats/{ID}/read"),
]


@pytest.mark.parametrize(("method", "path"), CHAT_ROUTES)
def test_chat_routes_need_a_session_and_reach_ms_matching(monkeypatch, method, path):
    upstream = Upstream()
    with gateway(monkeypatch, "matching", upstream) as client:
        assert client.request(method, path).status_code == 401
        assert upstream.calls == []
        body = b'{"type": "LOCATION", "latitude": -33.58, "longitude": -70.63}'
        response = client.request(method, path, headers={**token_headers(), "X-User-ID": "forged"}, content=body if method == "POST" else None)
    assert response.status_code == 200
    [call] = upstream.calls
    assert (call.url.host, call.url.port) == ("ms_matching", 8002)
    assert call.url.path == path.split("?")[0] and call.method == method
    assert "x-user-id" not in call.headers and call.headers["authorization"].startswith("Bearer ")
    if method == "POST":
        assert call.content == body


# -- blocks and muted people (served by Ms_Users) -------------------------------------------------------------------


USERS_ROUTES = [
    ("POST", "/api/v1/users/block"),
    ("DELETE", f"/api/v1/users/block/{ID}"),
    ("GET", "/api/v1/users/blocks/me"),
    ("GET", "/api/v1/users/chat-preferences"),
    ("PATCH", "/api/v1/users/chat-preferences"),
    ("GET", "/api/v1/users/location"),
    ("PUT", "/api/v1/users/location"),
    ("DELETE", "/api/v1/users/location"),
]


@pytest.mark.parametrize(("method", "path"), USERS_ROUTES)
def test_block_and_mute_routes_need_a_session_and_reach_ms_users(monkeypatch, method, path):
    upstream = Upstream(httpx.Response(204) if method == "DELETE" else None)
    with gateway(monkeypatch, "users", upstream) as client:
        assert client.request(method, path).status_code == 401
        assert upstream.calls == []
        response = client.request(method, path, headers=token_headers(), content=b'{"blockedUserId": "x"}' if method in ("POST", "PATCH", "PUT") else None)
    assert response.status_code == (204 if method == "DELETE" else 200)
    [call] = upstream.calls
    assert call.url.path == path and call.method == method and (call.url.host, call.url.port) == ("ms_users", 8001)


def test_the_block_check_for_other_services_is_not_exposed_to_the_app(monkeypatch):
    upstream = Upstream()
    with gateway(monkeypatch, "users", upstream) as client:
        response = client.get(f"/api/v1/users/blocks/check?userA={ID}&userB={OTHER}", headers=token_headers())
    assert response.status_code == 404 and upstream.calls == []


def test_openapi_documents_the_new_users_routes(monkeypatch):
    with gateway(monkeypatch, "users", Upstream()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    delete = paths["/api/v1/users/block/{blocked_user_id}"]["delete"]
    assert delete["parameters"][0] == {"name": "blocked_user_id", "in": "path", "required": True,
                                       "schema": {"type": "string", "format": "uuid"}}
    assert "blockedUserId" in paths["/api/v1/users/block"]["post"]["requestBody"]["content"]["application/json"]["example"]
    assert "muteUserIds" in paths["/api/v1/users/chat-preferences"]["patch"]["requestBody"]["content"]["application/json"]["example"]
