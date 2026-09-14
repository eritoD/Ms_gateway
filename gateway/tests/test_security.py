"""Tests for gateway authentication and cross-cutting HTTP protections."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings
from app.main import app
from app.services.token_service import TokenService

TEST_SECRET = "test-only-secret-with-at-least-thirty-two-randomish-bytes"


def security_settings() -> Settings:
    return Settings(
        auth_required=True,
        jwt_algorithm="HS256",
        jwt_issuer="sportmatch-auth",
        jwt_audience="sportmatch-mobile",
        jwt_secret=SecretStr(TEST_SECRET),
        jwt_public_key=None,
    )


@pytest.fixture
def authenticated_client() -> Iterator[TestClient]:
    settings = security_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def create_access_token(
    *,
    roles: list[str] | None = None,
    scope: str = "profile:read",
    audience: str = "sportmatch-mobile",
    token_type: str = "access",
    expires_delta: timedelta = timedelta(minutes=5),
    algorithm: str = "HS256",
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "user-123",
            "iss": "sportmatch-auth",
            "aud": audience,
            "iat": now,
            "exp": now + expires_delta,
            "token_type": token_type,
            "roles": roles or ["user"],
            "scope": scope,
        },
        TEST_SECRET,
        algorithm=algorithm,
    )


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_protected_route_requires_bearer_token(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/api/v1/security/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_route_fails_closed_without_verification_key() -> None:
    settings = Settings(
        auth_required=False,
        jwt_algorithm="HS256",
        jwt_secret=None,
        jwt_public_key=None,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/security/me",
                headers=bearer_headers(create_access_token()),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "La validación JWT no está configurada"}


def test_valid_access_token_returns_principal(
    authenticated_client: TestClient,
) -> None:
    token = create_access_token(roles=["user", "club_admin"], scope="profile:read clubs:write")

    response = authenticated_client.get(
        "/api/v1/security/me",
        headers=bearer_headers(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "subject": "user-123",
        "roles": ["club_admin", "user"],
        "scopes": ["clubs:write", "profile:read"],
    }


@pytest.mark.parametrize(
    ("token", "expected_status"),
    [
        (create_access_token(audience="another-application"), 401),
        (create_access_token(expires_delta=timedelta(minutes=-5)), 401),
        (create_access_token(token_type="refresh"), 401),
        (create_access_token(algorithm="HS384"), 401),
    ],
)
def test_invalid_tokens_are_rejected(
    authenticated_client: TestClient,
    token: str,
    expected_status: int,
) -> None:
    response = authenticated_client.get(
        "/api/v1/security/me",
        headers=bearer_headers(token),
    )

    assert response.status_code == expected_status
    assert response.headers["www-authenticate"] == "Bearer"


def test_admin_route_requires_admin_role(
    authenticated_client: TestClient,
) -> None:
    token = create_access_token(roles=["user"], scope="gateway:read")

    response = authenticated_client.get(
        "/api/v1/security/admin-check",
        headers=bearer_headers(token),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Rol insuficiente"}


def test_admin_route_requires_gateway_scope(
    authenticated_client: TestClient,
) -> None:
    token = create_access_token(roles=["admin"], scope="profile:read")

    response = authenticated_client.get(
        "/api/v1/security/admin-check",
        headers=bearer_headers(token),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Permisos insuficientes"}


def test_admin_route_accepts_role_and_scope(
    authenticated_client: TestClient,
) -> None:
    token = create_access_token(roles=["admin"], scope="gateway:read")

    response = authenticated_client.get(
        "/api/v1/security/admin-check",
        headers=bearer_headers(token),
    )

    assert response.status_code == 200
    assert response.json() == {"authorized": True, "subject": "user-123"}


def test_gateway_adds_defensive_headers_and_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/prueba", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == (
        "camera=(), geolocation=(), microphone=()"
    )


def test_gateway_replaces_unsafe_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/prueba", headers={"X-Request-ID": "invalid id!"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "invalid id!"
    assert len(response.headers["x-request-id"]) == 32


def test_gateway_rejects_untrusted_host() -> None:
    with TestClient(app) as client:
        response = client.get("/", headers={"Host": "attacker.example"})

    assert response.status_code == 400


def test_openapi_declares_bearer_security() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    bearer = schema["components"]["securitySchemes"]["JWTBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/security/me"]["get"]["security"]


def test_auth_required_rejects_missing_hs256_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            auth_required=True,
            jwt_algorithm="HS256",
            jwt_secret=None,
        )


def test_rs256_token_is_verified_with_public_key() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    settings = Settings(
        auth_required=True,
        jwt_algorithm="RS256",
        jwt_public_key=public_pem.decode("utf-8"),
        jwt_secret=None,
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "user-rsa",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "token_type": "access",
            "roles": ["user"],
            "scope": "profile:read",
        },
        private_pem,
        algorithm="RS256",
    )

    principal = TokenService(settings).decode_access_token(token)

    assert principal.subject == "user-rsa"
    assert principal.roles == frozenset({"user"})
    assert principal.scopes == frozenset({"profile:read"})
