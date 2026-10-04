"""Expose the users contract through the gateway, preserving upstream responses."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies.security import get_current_principal
from app.api.dependencies.services import get_users_service
from app.services.users_service import UsersService

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

BODY_EXAMPLES = {
    "/auth/register": {"email": "nuevo@example.com", "password": "UnaClaveDePrueba2026!", "nombre": "Ana", "apellido_paterno": "Prueba"},
    "/auth/login": {"email": "usuario02@sportmach.example.com", "password": "SportmachDemo2026!"},
    "/auth/email-verification/request": {"email": "nuevo@example.com"},
    "/auth/email-verification/confirm": {"email": "nuevo@example.com", "code": "123456"},
    "/auth/password-reset/request": {"email": "usuario02@sportmach.example.com"},
    "/auth/password-reset/confirm": {"email": "usuario02@sportmach.example.com", "code": "123456", "new_password": "UnaClaveNueva2026!"},
    "/{user_id}/profile": {"nombre": "Ana", "apellido_paterno": "Prueba", "biografia": "Me gusta el tenis"},
    "/{user_id}/preferences": {"deportes": [{"deporte_codigo": "tenis", "nivel": 3}], "disponibilidad": [{"dia_semana": "lunes", "hora_inicio": "18:00", "hora_fin": "20:00"}]},
    "/{user_id}/consents": {"type": "privacy", "purpose": "Uso de preferencias", "document_version": "v1", "method": "app"},
    "/athletes/cards": ["10000000-0000-4000-8000-000000000002"],
}


async def proxy_users(request: Request, service: Annotated[UsersService, Depends(get_users_service)]) -> Response:
    return await forward_request(request, service)


async def forward_request(request: Request, service: UsersService) -> Response:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_048_576:
            raise HTTPException(status_code=413, detail="Solicitud demasiado grande")
    headers = {name: request.headers[name] for name in ("authorization", "content-type", "accept") if name in request.headers}
    headers["x-request-id"] = request.state.request_id
    upstream = await service.forward(
        request.method, request.url.path,
        query=request.scope["query_string"], content=bytes(body), headers=headers,
    )
    # HTTPX has already decoded compressed content. Response recalculates its
    # length; do not relay content-encoding or hop-by-hop headers.
    response_headers = {name: upstream.headers[name] for name in (
        "content-type", "www-authenticate", "allow", "cache-control", "content-disposition",
    ) if name in upstream.headers}
    response_headers["cache-control"] = "no-store"
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


for path, methods, public in [
    ("/auth/register", ["POST"], True),
    ("/auth/login", ["POST"], True),
    ("/auth/email-verification/request", ["POST"], True),
    ("/auth/email-verification/confirm", ["POST"], True),
    ("/auth/password-reset/request", ["POST"], True),
    ("/auth/password-reset/confirm", ["POST"], True),
    ("/suggestions", ["GET"], False),
    ("/athletes/cards", ["POST"], False),
    ("/athletes/{user_id}", ["GET"], False),
    ("/{user_id}/profile", ["GET", "PUT"], False),
    ("/{user_id}/roles", ["GET"], False),
    ("/{user_id}/preferences", ["GET", "PUT"], False),
    ("/{user_id}/consents", ["GET", "POST"], False),
    ("/{user_id}/consents/{consent_id}", ["DELETE"], False),
    ("/{user_id}/exports", ["GET"], False),
    ("/{user_id}", ["DELETE"], False),
]:
    for method in methods:
        documentation = {
            "parameters": [
                {"name": name, "in": "path", "required": True,
                 "schema": {"type": "string", "format": "uuid"}}
                for name in ("user_id", "consent_id") if "{" + name + "}" in path
            ],
        }
        if path == "/suggestions":
            documentation.update({
                "summary": "Listar otros deportistas registrados",
                "description": "Cards públicas para Inicio y Descubrir. Solo deportistas "
                "activos y verificados (player o usuario), excluyendo la cuenta que consulta.",
            })
            documentation["parameters"].append({
                "name": "limit", "in": "query", "required": False,
                "schema": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
            })
        if method in ("POST", "PUT"):
            documentation["requestBody"] = {
                "required": True, "content": {"application/json": {
                    "schema": {"type": "array", "items": {"type": "string", "format": "uuid"}, "maxItems": 100}
                    if path == "/athletes/cards" else {"type": "object"}, "example": BODY_EXAMPLES[path],
                }},
            }
        router.add_api_route(
            path, proxy_users, methods=[method], response_class=Response,
            dependencies=[] if public else [Depends(get_current_principal)],
            name=f"users_{method.lower()}_{path.strip('/').replace('/', '_')}",
            openapi_extra=documentation,
        )
