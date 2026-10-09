from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response
from app.api.dependencies.security import get_current_principal
from app.api.dependencies.services import get_activities_service
from app.api.routes.users import forward_request
from app.services.activities_service import ActivitiesService

router = APIRouter(prefix="/api/v1/activities", tags=["Activities"], dependencies=[Depends(get_current_principal)])


async def proxy(request: Request, service: Annotated[ActivitiesService, Depends(get_activities_service)]):
    return await forward_request(request, service)


router.add_api_route("", proxy, methods=["GET"], response_class=Response,
    summary="Listar próximas actividades", openapi_extra={"parameters": [
        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
        {"name": "cursor", "in": "query", "schema": {"type": "string", "format": "uuid"}},
    ]})
router.add_api_route("", proxy, methods=["POST"], response_class=Response, status_code=201,
    summary="Publicar una actividad", openapi_extra={"requestBody": {"required": True,
        "content": {"application/json": {"schema": {"type": "object"}, "example": {
            "client_activity_id": "20000000-0000-4000-8000-000000000001", "title": "Running en el parque",
            "sport_code": "running", "starts_at": "2027-01-10T19:00:00-03:00",
            "location": "Parque Bicentenario, entrada principal", "description": "Trote recreativo de 5 km.",
            "capacity": 10,
        }}}}})
router.add_api_route("/{activity_id}", proxy, methods=["GET"], response_class=Response,
    summary="Ver detalle de una actividad", openapi_extra={"parameters": [
        {"name": "activity_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
    ]})
router.add_api_route("/{activity_id}", proxy, methods=["PATCH"], response_class=Response,
    summary="Modificar una actividad (solo organizador)", openapi_extra={
        "parameters": [{"name": "activity_id", "in": "path", "required": True,
                        "schema": {"type": "string", "format": "uuid"}}],
        "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"},
            "example": {"title": "Running largo en el parque", "starts_at": "2027-01-10T20:00:00-03:00", "capacity": 12},
        }}}})
router.add_api_route("/{activity_id}", proxy, methods=["DELETE"], response_class=Response, status_code=204,
    summary="Eliminar (cancelar) una actividad (solo organizador)", openapi_extra={"parameters": [
        {"name": "activity_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
    ]})
for path, method, status_code, summary in [
    ("/{activity_id}/applications", "POST", 201, "Postular a una actividad (queda pendiente)"),
    ("/{activity_id}/applications/me", "GET", 200, "Ver el estado de mi postulación"),
    ("/{activity_id}/applications", "GET", 200, "Ver postulaciones recibidas (solo organizador)"),
    ("/{activity_id}/applications/{application_id}/accept", "POST", 200, "Aceptar una postulación y descontar un cupo"),
    ("/{activity_id}/applications/{application_id}/reject", "POST", 200, "Rechazar una postulación"),
]:
    router.add_api_route(path, proxy, methods=[method], response_class=Response, status_code=status_code,
        summary=summary, name=f"activities_{method.lower()}_{path.strip('/').replace('/', '_')}",
        openapi_extra={"parameters": [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}
            for name in ("activity_id", "application_id") if "{" + name + "}" in path
        ]})
