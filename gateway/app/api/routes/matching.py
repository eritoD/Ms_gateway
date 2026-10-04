from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies.security import get_current_principal
from app.api.dependencies.services import get_matching_service
from app.api.routes.users import forward_request
from app.services.matching_service import MatchingService

router = APIRouter(prefix="/api/v1/matching", tags=["Matching"], dependencies=[Depends(get_current_principal)])


async def proxy(request: Request, service: Annotated[MatchingService, Depends(get_matching_service)]):
    return await forward_request(request, service)


for path, methods, summary, example in [
    ("/state", ["GET"], "Solicitudes recibidas, enviadas y matches", None),
    ("/requests", ["POST"], "Enviar solicitud con un like", {"recipient_id": "10000000-0000-4000-8000-000000000002"}),
    ("/requests/{request_id}/decision", ["POST"], "Aceptar o rechazar una solicitud", {"action": "accept"}),
    ("/requests/{request_id}", ["DELETE"], "Cancelar una solicitud enviada pendiente", None),
    ("/matches/{match_id}", ["GET"], "Consultar un match aceptado", None),
    ("/matches/{match_id}/messages", ["GET", "POST"], "Leer o enviar mensajes del match", {
        "client_message_id": "20000000-0000-4000-8000-000000000001", "text": "¡Hola! ¿Entrenamos?"}),
]:
    for method in methods:
        docs = {"parameters": [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}
            for name in ("request_id", "match_id") if "{" + name + "}" in path
        ]}
        if method == "GET" and path.endswith("/messages"):
            docs["parameters"] += [
                {"name": name, "in": "query", "schema": {"type": "integer", "minimum": 0}}
                for name in ("after_id", "before_id", "limit")
            ]
        if method == "POST":
            docs["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"type": "object"}, "example": example,
            }}}
        router.add_api_route(path, proxy, methods=[method], response_class=Response,
                             summary=summary, openapi_extra=docs)
