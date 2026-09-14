"""JWT validation service, independent from FastAPI's HTTP layer."""

from dataclasses import dataclass
from typing import Any

import jwt
from jwt.exceptions import InvalidKeyError, InvalidTokenError

from app.core.config import Settings


class SecurityConfigurationError(RuntimeError):
    """Raised when the gateway cannot securely verify access tokens."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated identity extracted from a verified access token."""

    subject: str
    roles: frozenset[str]
    scopes: frozenset[str]


class TokenService:
    """Validate JWT access tokens and expose trusted authorization claims."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _verification_key(self) -> str:
        if self._settings.jwt_algorithm == "HS256":
            secret = (
                self._settings.jwt_secret.get_secret_value()
                if self._settings.jwt_secret
                else ""
            )
            if len(secret.encode("utf-8")) < 32:
                raise SecurityConfigurationError(
                    "JWT_SECRET is missing or shorter than 32 bytes"
                )
            return secret

        if not self._settings.jwt_public_key:
            raise SecurityConfigurationError("JWT_PUBLIC_KEY is missing")

        return self._settings.jwt_public_key.replace("\\n", "\n")

    @staticmethod
    def _claim_values(
        claims: dict[str, Any],
        name: str,
        *,
        space_delimited: bool = False,
    ) -> frozenset[str]:
        raw_value = claims.get(name)
        if raw_value is None:
            return frozenset()

        if isinstance(raw_value, str):
            values = raw_value.split() if space_delimited else [raw_value]
        elif isinstance(raw_value, list):
            values = raw_value
        else:
            raise InvalidTokenError(f"Claim {name} has an invalid type")

        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise InvalidTokenError(f"Claim {name} contains an invalid value")

        return frozenset(value.strip() for value in values)

    def decode_access_token(self, token: str) -> Principal:
        """Verify a JWT and convert its claims into an authenticated principal."""

        key = self._verification_key()

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[self._settings.jwt_algorithm],
                audience=self._settings.jwt_audience,
                issuer=self._settings.jwt_issuer,
                leeway=self._settings.jwt_leeway_seconds,
                options={
                    "require": ["sub", "iss", "aud", "iat", "exp", "token_type"],
                },
            )
        except InvalidKeyError as exc:
            raise SecurityConfigurationError(
                "The JWT verification key is invalid"
            ) from exc

        if claims.get("token_type") != "access":
            raise InvalidTokenError("Only access tokens are accepted")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidTokenError("Claim sub is invalid")

        roles = self._claim_values(claims, "roles")
        scopes = self._claim_values(claims, "scope", space_delimited=True)
        scopes = scopes.union(self._claim_values(claims, "scopes"))

        return Principal(
            subject=subject.strip(),
            roles=roles,
            scopes=frozenset(scopes),
        )
