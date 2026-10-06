"""Environment-based settings for the gateway."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated gateway configuration with safe development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "SportMatch Gateway"
    app_version: str = "0.1.0"
    app_env: str = "development"
    service_name: str = "sportmatch-gateway"
    gateway_host: str = "0.0.0.0"
    gateway_port: int = Field(default=8000, ge=1, le=65535)
    users_service_url: HttpUrl | None = None
    matching_service_url: HttpUrl | None = None
    activities_service_url: HttpUrl | None = None
    users_timeout_seconds: float = Field(default=10, gt=0, le=120)

    @field_validator("users_service_url", "matching_service_url", "activities_service_url")
    @classmethod
    def validate_users_origin(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value and (value.username or value.password or value.query or value.fragment or value.path not in (None, "/")):
            raise ValueError("USERS_SERVICE_URL must contain only the service origin")
        return value

    auth_required: bool = False
    jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    jwt_issuer: str = "sportmatch-auth"
    jwt_audience: str = "sportmatch-mobile"
    jwt_secret: SecretStr | None = None
    jwt_public_key: str | None = None
    jwt_leeway_seconds: int = Field(default=5, ge=0, le=60)

    allowed_hosts: str = "localhost,127.0.0.1,gateway,testserver"
    cors_allowed_origins: str = ""
    enable_hsts: bool = False

    @field_validator("jwt_secret", "jwt_public_key", mode="before")
    @classmethod
    def empty_security_values_are_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_required_authentication_key(self) -> "Settings":
        if not self.auth_required:
            return self

        if self.jwt_algorithm == "HS256":
            secret = self.jwt_secret.get_secret_value() if self.jwt_secret else ""
            if len(secret.encode("utf-8")) < 32:
                raise ValueError(
                    "JWT_SECRET must contain at least 32 bytes when AUTH_REQUIRED=true"
                )
        elif not self.jwt_public_key:
            raise ValueError(
                "JWT_PUBLIC_KEY is required for RS256 when AUTH_REQUIRED=true"
            )

        return self

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Load and cache settings from the process environment."""

    return Settings()
