"""Liveness and readiness HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.services import get_health_service, get_users_service
from app.core.config import Settings, get_settings
from app.schemas.health import LiveHealthResponse, ReadyHealthResponse
from app.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=LiveHealthResponse,
    summary="Comprobar que el gateway está vivo",
)
async def live(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> LiveHealthResponse:
    status = service.get_liveness()
    return LiveHealthResponse(status=status.status, service=status.service)


@router.get(
    "/ready",
    response_model=ReadyHealthResponse,
    summary="Comprobar que el gateway está preparado",
)
async def ready(
    request: Request,
    service: Annotated[HealthService, Depends(get_health_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadyHealthResponse:
    if settings.users_service_url is not None:
        await get_users_service(request).check_ready()
    status = service.get_readiness()
    return ReadyHealthResponse(status=status.status, service=status.service)
