"""Exercise development-only payment decisions through the local gateway.

Requires PAYMENT_SMOKE_PLAYER_EMAIL, PAYMENT_SMOKE_CLUB_EMAIL and
PAYMENT_SMOKE_PASSWORD. Refuses to confirm anything except MOCK transactions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

BASE = "http://127.0.0.1:8000"


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != 8000:
        raise ValueError("Development smoke tests only follow the local gateway")
    return value


def main() -> None:
    password = os.environ["PAYMENT_SMOKE_PASSWORD"]
    results = []
    with httpx.Client(base_url=BASE, timeout=25, trust_env=False, follow_redirects=False) as client:
        assert client.get("/health/ready").status_code == 200
        plans_response = client.get("/api/v1/payments/plans")
        assert plans_response.status_code == 200
        plans = plans_response.json()
        assert client.post("/api/v1/payments/webpay/initiate", json={"plan_id": plans[0]["id"]}).status_code == 401
        for role, kind, key in [("deportista", "USUARIO", "PLAYER"), ("club", "CLUB", "CLUB")]:
            login = client.post("/api/v1/users/auth/login", json={
                "email": os.environ[f"PAYMENT_SMOKE_{key}_EMAIL"], "password": password})
            assert login.status_code == 200, f"Login failed for {role}"
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            plan = next(p for p in plans if p["tipo"] == kind and p["precio"] > 0)
            forged = client.post("/api/v1/payments/webpay/initiate", headers=headers,
                                 json={"plan_id": plan["id"], "amount": 1})
            assert forged.status_code == 400
            active = None
            for action, expected in [("aprobar", "APPROVED"), ("rechazar", "REJECTED"), ("cancelar", "CANCELLED")]:
                started = client.post("/api/v1/payments/webpay/initiate", headers=headers,
                                      json={"plan_id": plan["id"], "client": "web"})
                assert started.status_code == 201, f"Initiation failed for {role}: {started.status_code}"
                transaction = started.json()
                assert transaction["environment"] == "MOCK", "Refusing to confirm a non-MOCK transaction"
                assert transaction["amount"] == plan["precio"]
                checkout = client.get(local_url(transaction["checkout_url"]))
                assert checkout.status_code == 200 and 'method="POST"' in checkout.text
                assert "content-security-policy" in checkout.headers
                page = client.get(local_url(transaction["url"]), params={"token_ws": transaction["token_ws"]})
                assert page.status_code == 200
                decision = client.get("/api/v1/payments/webpay/mock/decision",
                                      params={"token_ws": transaction["token_ws"], "accion": action})
                assert decision.status_code == 303
                # The browser callback has no Authorization header; the service confirms server-side.
                callback = client.get(local_url(decision.headers["location"]))
                assert callback.status_code == 303
                assert callback.headers["location"].startswith("http://localhost:3001/pago/resultado?")
                receipt = client.post("/api/v1/payments/webpay/commit", headers=headers,
                                     json={"token_ws": transaction["token_ws"]})
                expected_status = 200 if expected == "APPROVED" else 400
                assert receipt.status_code == expected_status
                data = receipt.json() if expected_status == 200 else receipt.json()["transaccion"]
                assert data["status"] == expected
                repeat = client.post("/api/v1/payments/webpay/commit", headers=headers,
                                    json={"token_ws": transaction["token_ws"]})
                assert repeat.status_code == expected_status and repeat.json() == receipt.json()
                subscription = client.get("/api/v1/payments/subscription/me", headers=headers)
                assert subscription.status_code == 200
                current = subscription.json()
                if expected == "APPROVED":
                    active = current
                else:
                    assert current == active, "Rejected/cancelled payments changed the active subscription"
                results.append({"comprador": role, "decision": action, "estado": expected,
                                "monto_desde_catalogo": True, "reintento_idempotente": True})
                print(f"OK {role}: {expected}, callback and retry")
    destination = Path(__file__).resolve().parents[2] / "docs" / "verificacion-ms-pago.json"
    destination.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
