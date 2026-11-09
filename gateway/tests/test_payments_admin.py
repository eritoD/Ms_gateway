"""HU-57: pagos de la plataforma para el administrador, a través del gateway (solo lectura y solo admin)."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from tests.test_clubs_proxy import Upstream, gateway
from tests.test_users_proxy import SECRET, USER_ID, token_headers

PATH = "/api/v1/payments/admin/transactions"


def headers_with_roles(*roles: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "sub": USER_ID, "iss": "sportmatch-auth", "aud": "sportmatch-mobile",
        "iat": now, "exp": now + timedelta(minutes=10), "token_type": "access", "roles": list(roles),
    }, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_the_admin_reaches_ms_pago_with_every_filter(monkeypatch):
    upstream = Upstream()
    query = "estado=APPROVED&desde=2026-03-01&hasta=2026-03-31&categoria=CONVENIO&tipo=CLUB&page=2&limit=200"
    with gateway(monkeypatch, "payments", upstream) as client:
        response = client.get(f"{PATH}?{query}", headers=headers_with_roles("admin"))
    assert response.status_code == 200
    assert len(upstream.calls) == 1
    assert str(upstream.calls[0].url) == f"http://ms_pago:8005{PATH}?{query}"
    assert upstream.calls[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.parametrize("roles", [("player",), ("club_admin",), ("usuario",), ()])
def test_a_non_admin_is_refused_at_the_gateway(monkeypatch, roles):
    upstream = Upstream()
    with gateway(monkeypatch, "payments", upstream) as client:
        assert client.get(PATH, headers=headers_with_roles(*roles)).status_code == 403
        assert client.get(PATH, headers=token_headers()).status_code == 403
        assert client.get(PATH).status_code == 401
    assert upstream.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_payments_cannot_be_changed_through_the_gateway(monkeypatch, method):
    upstream = Upstream()
    with gateway(monkeypatch, "payments", upstream) as client:
        admin = headers_with_roles("admin")
        assert client.request(method, PATH, headers=admin).status_code in (404, 405)
        assert client.request(method, f"{PATH}/00000000-0000-0000-0000-000000000000", headers=admin).status_code in (404, 405)
    assert upstream.calls == []


def test_the_route_is_documented_and_protected(monkeypatch):
    with gateway(monkeypatch, "payments", Upstream()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    operations = paths[PATH]
    assert set(operations) == {"get"}
    assert "security" in operations["get"]
    names = {p["name"] for p in operations["get"]["parameters"]}
    assert names == {"estado", "desde", "hasta", "categoria", "tipo", "page", "limit"}
