"""Expose the users contract through the gateway, preserving upstream responses."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies.security import get_current_principal
from app.api.dependencies.services import get_users_service
from app.core.config import get_settings
from app.services.users_service import UsersService

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

BODY_EXAMPLES = {
    "/auth/register": {"email": "nuevo@example.com", "password": "UnaClaveDePrueba2026!", "nombre": "Ana", "apellido_paterno": "Prueba"},
    "/auth/register-club": {"email": "representante@club.example.com", "password": "UnaClaveDePrueba2026!", "nombre": "Rocio", "apellido_paterno": "Soto"},
    "/auth/login": {"email": "usuario02@sportmach.example.com", "password": "SportmachDemo2026!"},
    "/auth/email-verification/request": {"email": "nuevo@example.com"},
    "/auth/email-verification/confirm": {"email": "nuevo@example.com", "code": "123456"},
    "/auth/password-reset/request": {"email": "usuario02@sportmach.example.com"},
    "/auth/password-reset/confirm": {"email": "usuario02@sportmach.example.com", "code": "123456", "new_password": "UnaClaveNueva2026!"},
    "/{user_id}/profile": {"nombre": "Ana", "apellido_paterno": "Prueba", "biografia": "Me gusta el tenis"},
    "/{user_id}/preferences": {"deportes": [{"deporte_codigo": "tenis", "nivel": 3}], "disponibilidad": [{"dia_semana": "lunes", "hora_inicio": "18:00", "hora_fin": "20:00"}]},
    "/{user_id}/consents": {"type": "privacy", "purpose": "Uso de preferencias", "document_version": "v1", "method": "app"},
    "/athletes/cards": ["10000000-0000-4000-8000-000000000002"],
    "/reports": {"tipo": "usuario", "objetivo_id": "10000000-0000-4000-8000-000000000002", "objetivo_nombre": "Ana P.", "motivo": "Spam o publicidad", "detalle": "Me escribió de madrugada"},
    "/admin/reports/{report_id}": {"estado": "escalado", "motivo": "Reincidente, pasa a sanción"},
    "/admin/sanctions": {"usuario_id": "10000000-0000-4000-8000-000000000002", "tipo": "temporal", "dias": 7, "motivo": "Acoso reiterado"},
    "/admin/sanctions/{sanction_id}/lift": {"motivo": "Apelación aceptada"},
    "/block": {"blockedUserId": "10000000-0000-4000-8000-000000000002"},
    "/location": {"latitud": -33.45694, "longitud": -70.64827, "precision_m": 15},
    "/chat-preferences": {"muteUserIds": ["10000000-0000-4000-8000-000000000002"], "unmuteUserIds": []},
}


# HU-61: the photo upload is the only request allowed to exceed 1 MiB; the photo itself is served with its own cache rules.
_PHOTO_UPLOAD = re.compile(r"/api/v1/users/[^/]+/profile/photo")
_PHOTO_FILE = re.compile(r"/api/v1/users/photos/[^/]+")
_DEFAULT_BODY_LIMIT = 1_048_576
_SANCTION_CHANGE = re.compile(r"/api/v1/users/admin/sanctions(/[^/]+/lift)?")


async def proxy_users(request: Request, service: Annotated[UsersService, Depends(get_users_service)]) -> Response:
    return await forward_request(request, service)


async def forward_request(request: Request, service: UsersService) -> Response:
    limit = _DEFAULT_BODY_LIMIT
    if request.method == "PUT" and _PHOTO_UPLOAD.fullmatch(request.url.path):
        limit = get_settings().photo_upload_max_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Solicitud demasiado grande")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
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
        "content-security-policy", "referrer-policy",
    ) if name in upstream.headers}
    if request.method == "GET" and upstream.status_code == 200 and _PHOTO_FILE.fullmatch(request.url.path):
        response_headers["cache-control"] = upstream.headers.get("cache-control", "no-store")
        response_headers["x-content-type-options"] = "nosniff"
    else:
        response_headers["cache-control"] = "no-store"
    if upstream.status_code < 400 and request.method == "POST" and _SANCTION_CHANGE.fullmatch(request.url.path):
        guard = getattr(request.app.state, "account_guard", None)
        if guard is not None:
            guard.invalidate()  # HU-45: a sanction applied or lifted takes effect now, not after the cache expires
    if upstream.status_code in (301, 302, 303, 307, 308) and "location" in upstream.headers:
        response_headers["location"] = upstream.headers["location"]
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


for path, methods, public in [
    # HU-61: foto de perfil. La imagen se pide sin sesión (la usan <img> y Image, que no envían el token): su clave
    # es aleatoria y solo la reciben quienes pueden ver el perfil. Subirla y quitarla exige sesión y cuenta propia.
    ("/photos/{photo_key}", ["GET"], True),
    ("/{user_id}/profile/photo", ["PUT", "DELETE"], False),
    ("/auth/register", ["POST"], True),
    ("/auth/register-club", ["POST"], True),
    ("/auth/login", ["POST"], True),
    ("/auth/email-verification/request", ["POST"], True),
    ("/auth/email-verification/confirm", ["POST"], True),
    ("/auth/password-reset/request", ["POST"], True),
    ("/auth/password-reset/confirm", ["POST"], True),
    ("/suggestions", ["GET"], False),
    ("/athletes/cards", ["POST"], False),
    ("/athletes/{user_id}", ["GET"], False),
    ("/me/blocks", ["GET"], False),
    ("/me/blocks/{user_id}", ["PUT", "DELETE"], False),
    # HU-51. /blocks/check is for other services (it answers only to one of the two people or an
    # admin) and is deliberately not exposed to the app.
    ("/block", ["POST"], False),
    ("/block/{blocked_user_id}", ["DELETE"], False),
    ("/blocks/me", ["GET"], False),
    ("/chat-preferences", ["GET", "PATCH"], False),
    # Live position for recommending nearby people (Ms_Users decides when it is worth saving a new one).
    ("/location", ["GET", "PUT", "DELETE"], False),
    ("/reports", ["POST"], False),
    ("/admin/reports", ["GET"], False),
    ("/admin/reports/{report_id}", ["PATCH"], False),
    # HU-45: sanciones. Ms_Users exige el rol admin. El estado de la cuenta (/internal/v1/accounts/...) nunca se declara aquí.
    ("/admin/sanctions", ["GET", "POST"], False),
    ("/admin/sanctions/{sanction_id}", ["GET"], False),
    ("/admin/sanctions/{sanction_id}/lift", ["POST"], False),
    # HU-48: historial de auditoría, solo lectura. Ms_Users exige el rol admin. Las rutas /internal de
    # Ms_Users nunca se declaran aquí (lo no declarado responde 404).
    ("/admin/audit-events", ["GET"], False),
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
                for name in ("user_id", "consent_id", "report_id", "sanction_id", "blocked_user_id") if "{" + name + "}" in path
            ],
        }
        if "{photo_key}" in path:
            documentation["parameters"].append({"name": "photo_key", "in": "path", "required": True,
                                                "schema": {"type": "string", "pattern": "^[0-9a-f]{32}[.](jpg|png)$"}})
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
            for name, schema, description in [
                ("radius_km", {"type": "number", "minimum": 1, "maximum": 100}, "Radio en km desde la ubicación guardada; requiere coordenadas propias."),
                ("sport", {"type": "string", "maxLength": 50}, "Código del deporte."),
                ("min_level", {"type": "integer", "minimum": 1, "maximum": 5, "default": 1}, "Nivel mínimo del deportista."),
                ("max_level", {"type": "integer", "minimum": 1, "maximum": 5, "default": 5}, "Nivel máximo del deportista."),
                ("shared_sports", {"type": "boolean", "default": False}, "Solo deportes en común."),
                ("level_tolerance", {"type": "integer", "minimum": 0, "maximum": 4}, "Diferencia máxima de nivel en el mismo deporte compartido."),
            ]:
                documentation["parameters"].append({"name": name, "in": "query", "required": False,
                                                     "schema": schema, "description": description})
        if method in ("POST", "PUT", "PATCH") and path in BODY_EXAMPLES:
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
