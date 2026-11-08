"""HU-45: the gateway refuses suspended accounts on every authenticated request and on the chat socket."""
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
import pytest

from app.core.config import get_settings
from app.services.account_guard import ClosedAccountGuard
from tests.test_chat_socket_proxy import FakeMatching, WS, auth_frame, access_token, closed_with, gateway, wait_until
from tests.test_users_proxy import SECRET, proxy_client, token_headers

SERVICE_TOKEN = "service-to-service-secret-with-32-bytes-min"
USER = str(uuid4())
PROFILE = f"/api/v1/users/{USER}/profile"
SUSPENDED = {"suspended": True, "tipo": "temporal", "motivo": "Acoso reiterado", "fin": "2026-11-08T12:30:00Z"}
FREE = {"suspended": False, "tipo": None, "motivo": None, "fin": None}


def bearer(sub=USER, roles=("player",)):
    now = datetime.now(timezone.utc)
    return {"Authorization": "Bearer " + jwt.encode({
        "sub": sub, "iss": "sportmatch-auth", "aud": "sportmatch-mobile", "iat": now, "exp": now + timedelta(minutes=10),
        "token_type": "access", "roles": list(roles)}, SECRET, algorithm="HS256")}


class Upstream:
    """A fake Ms_Users: answers the account check from `accounts` and records every call."""

    def __init__(self):
        self.accounts = {}
        self.status_calls = []
        self.other_calls = []
        self.down = False
        self.status_code = 200
        self.change_status = 200

    def __call__(self, request):
        if request.url.path.startswith("/internal/v1/accounts/"):
            self.status_calls.append(request)
            if self.down:
                raise httpx.ConnectError("down", request=request)
            account = request.url.path.split("/")[4]
            return httpx.Response(self.status_code, json=self.accounts.get(account, FREE))
        self.other_calls.append(request)
        if request.method == "POST" and "/admin/sanctions" in request.url.path:
            return httpx.Response(self.change_status, json={"id": "x"})
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    upstream = Upstream()
    with proxy_client(monkeypatch, upstream) as client:
        yield client, upstream


def test_a_suspended_account_is_refused_with_403_and_never_reaches_the_service(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    response = client.get(PROFILE, headers=bearer())
    assert response.status_code == 403
    body = response.json()
    assert set(body) == {"detail", "code", "tipo", "motivo", "fin"}
    assert (body["code"], body["tipo"], body["motivo"], body["fin"]) == (
        "account_suspended", "temporal", "Acoso reiterado", "2026-11-08T12:30:00Z")
    assert "08-11-2026" in body["detail"] and "Acoso reiterado" in body["detail"]
    assert upstream.other_calls == []
    request = upstream.status_calls[0]
    assert request.headers["x-internal-service-token"] == SERVICE_TOKEN
    assert request.url.path == f"/internal/v1/accounts/{USER}/status" and "authorization" not in request.headers


def test_a_permanent_suspension_says_so(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = {"suspended": True, "tipo": "permanente", "motivo": "Conducta grave", "fin": None}
    body = client.get(PROFILE, headers=bearer()).json()
    assert body["fin"] is None and "permanente" in body["detail"]


def test_other_accounts_and_admins_are_not_affected_and_admins_are_not_even_checked(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    other = str(uuid4())
    assert client.get(f"/api/v1/users/{other}/profile", headers=bearer(other)).status_code == 200
    admin = str(uuid4())
    assert client.get(f"/api/v1/users/{admin}/profile", headers=bearer(admin, ("admin",))).status_code == 200
    assert [r.url.path.split("/")[4] for r in upstream.status_calls] == [other]


def test_the_answer_is_cached_for_a_short_time(guarded):
    client, upstream = guarded
    for _ in range(3):
        assert client.get(PROFILE, headers=bearer()).status_code == 200
    assert len(upstream.status_calls) == 1
    client.app.state.account_guard._cache.clear()
    assert client.get(PROFILE, headers=bearer()).status_code == 200 and len(upstream.status_calls) == 2


def test_the_cache_expires(guarded):
    client, upstream = guarded
    clock = {"now": 1000.0}
    client.app.state.account_guard._clock = lambda: clock["now"]
    assert client.get(PROFILE, headers=bearer()).status_code == 200
    upstream.accounts[USER] = SUSPENDED
    clock["now"] += 5
    assert client.get(PROFILE, headers=bearer()).status_code == 200            # still the cached answer
    clock["now"] += 20                                                          # past the 15 s of cache
    assert client.get(PROFILE, headers=bearer()).status_code == 403


def test_applying_a_sanction_through_the_gateway_takes_effect_at_once(guarded):
    client, upstream = guarded
    assert client.get(PROFILE, headers=bearer()).status_code == 200            # "free" is now cached
    upstream.accounts[USER] = SUSPENDED
    admin = bearer(str(uuid4()), ("admin",))
    body = {"usuario_id": USER, "tipo": "permanente", "motivo": "Conducta grave"}
    upstream.change_status = 409                                                # a refused change drops nothing
    assert client.post("/api/v1/users/admin/sanctions", headers=admin, json=body).status_code == 409
    assert client.get(PROFILE, headers=bearer()).status_code == 200
    upstream.change_status = 201
    assert client.post("/api/v1/users/admin/sanctions", headers=admin, json=body).status_code == 201
    assert client.get(PROFILE, headers=bearer()).status_code == 403            # no waiting for the cache


def test_lifting_a_sanction_through_the_gateway_restores_access_at_once(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    assert client.get(PROFILE, headers=bearer()).status_code == 403            # "suspended" is now cached
    upstream.accounts[USER] = FREE
    assert client.get(PROFILE, headers=bearer()).status_code == 403            # still cached
    lift = client.post(f"/api/v1/users/admin/sanctions/{uuid4()}/lift", headers=bearer(str(uuid4()), ("admin",)),
                       json={"motivo": "Apelación aceptada"})
    assert lift.status_code == 200
    assert client.get(PROFILE, headers=bearer()).status_code == 200


def test_if_ms_users_does_not_answer_the_last_known_answer_is_used(guarded):
    client, upstream = guarded
    clock = {"now": 1000.0}
    client.app.state.account_guard._clock = lambda: clock["now"]
    upstream.accounts[USER] = SUSPENDED
    assert client.get(PROFILE, headers=bearer()).status_code == 403
    upstream.down = True
    clock["now"] += 300                                                         # expired, but within the stale limit
    assert client.get(PROFILE, headers=bearer()).status_code == 403
    clock["now"] += 3000                                                        # older than a token can live
    assert client.get(PROFILE, headers=bearer()).status_code == 503


def test_if_ms_users_does_not_answer_and_nothing_is_known_the_answer_is_503(guarded):
    client, upstream = guarded
    upstream.down = True
    response = client.get(PROFILE, headers=bearer())
    assert response.status_code == 503 and upstream.other_calls == []           # fails closed
    upstream.down = False
    assert client.get(PROFILE, headers=bearer()).status_code == 200


@pytest.mark.parametrize("status_code", [401, 403, 404, 500, 503])
def test_an_error_answer_from_ms_users_counts_as_not_knowing(guarded, status_code):
    client, upstream = guarded
    upstream.status_code = status_code
    assert client.get(PROFILE, headers=bearer()).status_code == 503 and upstream.other_calls == []


def test_public_routes_are_not_checked(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    assert client.post("/api/v1/users/auth/login", json={"email": "a@b.cl", "password": "x"}).status_code == 200
    assert upstream.status_calls == []                                          # Ms_Users itself refuses a sanctioned login


def test_without_the_service_secret_authenticated_routes_fail_closed(monkeypatch, caplog):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    upstream = Upstream()
    upstream.accounts[USER] = SUSPENDED
    with caplog.at_level("ERROR"):
        with proxy_client(monkeypatch, upstream, account_check=True) as client:
            assert isinstance(client.app.state.account_guard, ClosedAccountGuard)
            for headers in (token_headers(), bearer()):                         # a sanctioned account is never let through
                response = client.get(PROFILE, headers=headers)
                assert response.status_code == 503 and "code" not in response.json()
            assert client.get(PROFILE).status_code == 401                       # no token: still 401
            assert client.get(f"/api/v1/users/{uuid4()}/profile", headers=bearer(str(uuid4()), ("admin",))).status_code == 200
            assert client.post("/api/v1/users/auth/login", json={"email": "a@b.cl", "password": "x"}).status_code == 200
    assert upstream.status_calls == []                                          # nothing is asked without the secret
    assert "INTERNAL_SERVICE_TOKEN is not set" in caplog.text


def test_a_blank_service_secret_counts_as_missing(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "   ")
    with proxy_client(monkeypatch, Upstream(), account_check=True) as client:
        assert client.get(PROFILE, headers=bearer()).status_code == 503


def test_the_chat_socket_is_refused_without_the_service_secret(monkeypatch, matching):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("USERS_SERVICE_URL", "http://ms_users:8001")
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame(access_token(USER)))
            assert closed_with(ws) == 1013
    assert matching.connections == 0


# -- Ms_Users does not answer: no case lets a known suspended account through -----------------------------------

def test_ms_users_down_never_lets_a_known_suspended_account_through(guarded):
    client, upstream = guarded
    clock = {"now": 1000.0}
    client.app.state.account_guard._clock = lambda: clock["now"]
    upstream.accounts[USER] = SUSPENDED
    assert client.get(PROFILE, headers=bearer()).status_code == 403
    upstream.down = True
    for elapsed, expected in ((5, 403),          # fresh cache: answered from cache
                              (20, 403),         # expired but inside the stale window: last known answer, suspended
                              (3000, 503)):      # past the stale window: unknown, refused (503, not 200)
        clock["now"] = 1000.0 + elapsed
        response = client.get(PROFILE, headers=bearer())
        assert response.status_code == expected, elapsed
    assert upstream.other_calls == []


def test_ms_users_down_with_a_stale_free_answer_or_no_answer_cannot_open_a_sanction_gap_beyond_the_window(guarded):
    client, upstream = guarded
    clock = {"now": 1000.0}
    client.app.state.account_guard._clock = lambda: clock["now"]
    assert client.get(PROFILE, headers=bearer()).status_code == 200            # "free" cached
    upstream.down = True
    clock["now"] += 20
    assert client.get(PROFILE, headers=bearer()).status_code == 200            # stale free: tolerated, at most one token life
    clock["now"] += 3000
    assert client.get(PROFILE, headers=bearer()).status_code == 503            # too old: refused
    assert client.get(f"/api/v1/users/{uuid4()}/profile", headers=bearer(str(uuid4()))).status_code == 503  # nothing known


def test_a_sanction_applied_while_ms_users_is_down_is_never_answered_with_200(guarded):
    client, upstream = guarded
    assert client.get(PROFILE, headers=bearer()).status_code == 200
    upstream.accounts[USER] = SUSPENDED
    upstream.change_status = 201
    admin = bearer(str(uuid4()), ("admin",))
    assert client.post("/api/v1/users/admin/sanctions", headers=admin,
                       json={"usuario_id": USER, "tipo": "permanente", "motivo": "Conducta grave"}).status_code == 201
    upstream.down = True                                                        # the cache was dropped: nothing known
    assert client.get(PROFILE, headers=bearer()).status_code == 503


def test_the_internal_endpoints_are_not_reachable_through_the_gateway(guarded):
    client, upstream = guarded
    for path in (f"/internal/v1/accounts/{USER}/status", f"/api/v1/users/internal/v1/accounts/{USER}/status",
                 "/internal/v1/audit/events", "/internal/v1/accounts/names",
                 f"/internal/v1/activities/{USER}/author", f"/api/v1/activities/internal/v1/activities/{USER}/author",
                 f"/internal/v1/messages/{USER}/7/author", f"/api/v1/matching/internal/v1/messages/{USER}/7/author"):
        for method in ("get", "post"):
            response = getattr(client, method)(path, headers={**bearer(), "X-Internal-Service-Token": SERVICE_TOKEN})
            assert response.status_code in (404, 405), (method, path)
    assert upstream.other_calls == []


def test_sanction_routes_are_declared_forward_their_query_and_need_a_token(guarded):
    client, upstream = guarded
    admin = bearer(str(uuid4()), ("admin",))
    sanction_id = str(uuid4())
    for method, path in (("get", "/api/v1/users/admin/sanctions?estado=activa&limit=10&pagina=2"),
                         ("get", f"/api/v1/users/admin/sanctions/{sanction_id}"),
                         ("post", "/api/v1/users/admin/sanctions"), ("post", f"/api/v1/users/admin/sanctions/{sanction_id}/lift")):
        assert getattr(client, method)(path).status_code == 401, path
    assert upstream.other_calls == []
    assert client.get("/api/v1/users/admin/sanctions?estado=activa&limit=10&pagina=2", headers=admin).status_code == 200
    assert upstream.other_calls[-1].url.query == b"estado=activa&limit=10&pagina=2"
    for method in ("put", "patch", "delete"):
        for path in ("/api/v1/users/admin/sanctions", f"/api/v1/users/admin/sanctions/{sanction_id}",
                     f"/api/v1/users/admin/sanctions/{sanction_id}/lift"):
            assert getattr(client, method)(path, headers=admin).status_code in (404, 405), (method, path)


# -- chat WebSocket ------------------------------------------------------------------------------------------

@pytest.fixture
def matching():
    server = FakeMatching().start()
    yield server
    server.stop()


def with_users(client, upstream):
    client.app.state.users_client._transport = httpx.MockTransport(upstream)


def socket_gateway(monkeypatch, matching, ttl="1"):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("USERS_SERVICE_URL", "http://ms_users:8001")
    monkeypatch.setenv("ACCOUNT_STATUS_TTL_SECONDS", ttl)
    return gateway(monkeypatch, matching)


def test_a_suspended_account_cannot_open_the_chat_socket(monkeypatch, matching):
    upstream = Upstream()
    upstream.accounts[USER] = SUSPENDED
    with socket_gateway(monkeypatch, matching) as client:
        with_users(client, upstream)
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame(access_token(USER)))
            assert closed_with(ws) == 4423
        with client.websocket_connect(WS) as ws:                               # somebody else gets in
            ws.send_json(auth_frame(access_token(str(uuid4()))))
            assert ws.receive_json() == {"type": "ready"}
    assert matching.connections == 1                                            # only the second one reached the service


def test_an_open_chat_socket_is_closed_when_its_account_gets_suspended(monkeypatch, matching):
    upstream = Upstream()
    with socket_gateway(monkeypatch, matching) as client:
        with_users(client, upstream)
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame(access_token(USER)))
            assert ws.receive_json() == {"type": "ready"}
            upstream.accounts[USER] = SUSPENDED
            assert closed_with(ws) == 4423                                      # within about one cache period
        assert wait_until(lambda: matching.close_codes)                         # and the service side was closed too


def test_the_chat_socket_is_refused_when_the_account_cannot_be_checked(monkeypatch, matching):
    upstream = Upstream()
    upstream.down = True
    with socket_gateway(monkeypatch, matching) as client:
        with_users(client, upstream)
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame(access_token(USER)))
            assert closed_with(ws) == 1013
    assert matching.connections == 0


def test_a_suspended_club_cannot_edit_a_convocatoria(guarded):
    """HU-68: the edit route is a private route like the rest, so the HU-45 block applies to it."""
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    path = f"/api/v1/clubs/{uuid4()}/announcements/{uuid4()}"
    response = client.patch(path, headers=bearer(roles=("club_admin",)), json={"cupos": 3})
    assert response.status_code == 403 and response.json()["code"] == "account_suspended"
    assert upstream.other_calls == []
    assert client.patch(path, json={"cupos": 3}).status_code == 401
