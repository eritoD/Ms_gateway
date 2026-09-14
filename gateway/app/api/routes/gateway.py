"""Public gateway information routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.services import get_gateway_service
from app.schemas.gateway import GatewayInfoResponse, GatewayProbeResponse
from app.services.gateway_service import GatewayService

router = APIRouter(tags=["Gateway"])


@router.get(
    "/",
    response_model=GatewayInfoResponse,
    summary="Obtener información del gateway",
)
async def gateway_information(
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> GatewayInfoResponse:
    status = service.get_status()
    return GatewayInfoResponse(
        service=status.service,
        version=status.version,
        status=status.status,
    )


@router.get(
    "/prueba",
    response_model=GatewayProbeResponse,
    summary="Probar la respuesta del gateway",
)
async def gateway_probe(
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> GatewayProbeResponse:
    return GatewayProbeResponse(message=service.get_probe_message())
