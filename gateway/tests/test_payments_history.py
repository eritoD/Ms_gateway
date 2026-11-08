from uuid import uuid4

import httpx
import pytest

from tests.test_clubs_proxy import gateway, Upstream
from tests.test_users_proxy import token_headers


@pytest.mark.parametrize("path", ["history/me?page=2&page_size=5", "subscriptions/me/history",
                                  f"history/{uuid4()}/receipt"])
def test_historial_exige_jwt_y_se_envia_solo_a_pagos(monkeypatch, path):
    upstream = Upstream()
    with gateway(monkeypatch, "payments", upstream) as client:
        assert client.get(f"/api/v1/payments/{path}").status_code == 401
        assert not upstream.calls
        result = client.get(f"/api/v1/payments/{path}", headers=token_headers())
        assert result.status_code == 200
    assert len(upstream.calls) == 1
    assert str(upstream.calls[0].url).rstrip("?") == f"http://ms_pago:8005/api/v1/payments/{path}"
    assert upstream.calls[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.parametrize("status", [200, 404])
def test_comprobante_conserva_estado_cuerpo_y_descarga(monkeypatch, status):
    body = b"<html>SportMatch</html>" if status == 200 else b'{"detail":"Comprobante no encontrado."}'
    upstream = Upstream(httpx.Response(status, content=body, headers={
        "content-type": "text/html; charset=utf-8", "content-disposition": 'attachment; filename="comprobante.html"',
        "cache-control": "no-store"}))
    with gateway(monkeypatch, "payments", upstream) as client:
        result = client.get(f"/api/v1/payments/history/{uuid4()}/receipt", headers=token_headers())
    assert result.status_code == status and result.content == body
    assert result.headers["content-disposition"].startswith("attachment")
    assert result.headers["cache-control"] == "no-store"
