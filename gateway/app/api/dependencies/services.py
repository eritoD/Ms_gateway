"""Construct application services from validated configuration."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.core.config import Settings, get_settings
from app.services.gateway_service import GatewayService
from app.services.health_service import HealthService
from app.services.token_service import TokenService
from app.services.users_service import UsersService


def get_users_service(request: Request) -> UsersService:
    client = getattr(request.app.state, "users_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Ms_Users no está configurado")
    return UsersService(client)


def get_gateway_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GatewayService:
    return GatewayService(settings)


def get_health_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthService:
    return HealthService(settings)


def get_token_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenService:
    return TokenService(settings)
