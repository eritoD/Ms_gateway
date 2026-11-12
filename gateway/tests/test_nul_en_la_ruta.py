"""Defecto 3 de la verificación de HU-61/67/68: un `%00` en la ruta llegaba a httpx (`InvalidURL`) y respondía 500.
Ahora el gateway responde 404 antes de reenviar nada, para cualquier ruta, con o sin sesión."""
from __future__ import annotations

import pytest

from tests.test_clubs_proxy import Upstream, gateway
from tests.test_users_proxy import token_headers

RUTAS = [
    "/api/v1/users/photos/%00",
    "/api/v1/users/photos/0123456789abcdef0123456789abcdef.jpg%00.png",
    "/api/v1/clubs/%00",
    "/api/v1/clubs/announcements/%00",
    "/api/v1/payments/plans%00",
    "/api/v1/matching/%00",
    "/api/v1/activities/%00",
    "/api/v1/chats/%00",
    "/%00",
    "/api/v1/%00/x",
]


@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("servicio", ["users", "clubs", "payments"])
def test_a_nul_in_the_path_is_404_and_nothing_is_forwarded(monkeypatch, servicio, ruta):
    upstream = Upstream()
    with gateway(monkeypatch, servicio, upstream) as client:
        for headers in ({}, token_headers()):
            for metodo in ("get", "post", "put", "patch", "delete"):
                response = getattr(client, metodo)(ruta, headers=headers)
                assert response.status_code == 404, (metodo, ruta, response.status_code)
                assert "x-content-type-options" in response.headers        # sigue llevando las cabeceras de seguridad
    assert upstream.calls == []


def test_a_normal_path_is_still_forwarded(monkeypatch):
    upstream = Upstream()
    with gateway(monkeypatch, "clubs", upstream) as client:
        assert client.get("/api/v1/clubs/deportes").status_code == 200
    assert len(upstream.calls) == 1
