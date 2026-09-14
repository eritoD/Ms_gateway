"""Tests for the gateway's public HTTP contract."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_gateway_information() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "sportmatch-gateway",
        "version": "0.1.0",
        "status": "running",
    }


def test_gateway_test_route() -> None:
    response = client.get("/prueba")

    assert response.status_code == 200
    assert response.json() == {
        "message": "El gateway de SportMatch funciona correctamente",
    }


def test_liveness() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "sportmatch-gateway",
    }


def test_readiness() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "sportmatch-gateway",
    }


def test_unknown_route_returns_not_found() -> None:
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404


def test_openapi_contains_only_the_created_application_routes() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {"/", "/prueba", "/health/live", "/health/ready"}.issubset(paths)
    assert paths["/"]["get"]["tags"] == ["Gateway"]
    assert paths["/prueba"]["get"]["tags"] == ["Gateway"]
    assert paths["/health/live"]["get"]["tags"] == ["Health"]
    assert paths["/health/ready"]["get"]["tags"] == ["Health"]
