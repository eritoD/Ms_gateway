"""Expose ms-club (clubs, convocatorias, postulaciones, comunicados and plan payments) through the gateway.

Reading the directory and the convocatorias section is public (visitors browse them); everything that
acts on behalf of a person needs a valid access token, which ms-club validates again. Fixed paths are
registered before the ones with parameters, so a public `/{club_id}` can never swallow `/me`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies.security import get_current_principal
from app.api.dependencies.services import get_clubs_service
from app.api.routes.users import forward_request
from app.services.clubs_service import ClubsService

router = APIRouter(prefix="/api/v1/clubs", tags=["Clubs"])
payments_router = APIRouter(prefix="/api/v1/payments", tags=["Payments"])

UUID_EXAMPLE = "10000000-0000-4000-8000-000000000002"
PAGE = {
    "page": {"type": "integer", "minimum": 1, "default": 1},
    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
}
INTEGER_PATH_PARAMETERS = {"deporte_id"}
PATH_PARAMETERS = ("club_id", "convocatoria_id", "postulacion_id", "deporte_id", "usuario_id", "asignacion_id", "hilo_id")
MESSAGES = {
    "after_id": {"type": "integer", "minimum": 0},
    "before_id": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
}
MESSAGE_EXAMPLE = {"texto": "¿La prueba del sábado es abierta?", "client_message_id": "30000000-0000-4000-8000-000000000001"}

CLUB_EXAMPLE = {"nombre": "Club Deportivo Ñuñoa", "descripcion": "Fútbol y tenis para todas las edades.",
                "comuna": "Ñuñoa", "deporte_ids": [1, 2]}
CONVOCATORIA_EXAMPLE = {
    "deporte_id": 1, "categoria_edad": "Sub-17", "edad_minima": 15, "edad_maxima": 17,
    "fecha_prueba": "2026-11-07T10:00:00-03:00", "requisitos": ["Ficha médica al día"], "cupos": 40,
    "direccion": "Av. Grecia 2001", "titulo": "Prueba masiva Sub-17"}

# (path, methods, public, summary, query parameters, request body example)
CLUBS = [
    ("", ["GET"], True, "Directorio de clubes",
     {"nombre": {"type": "string"}, "deporte": {"type": "string"}, "deporte_id": {"type": "integer"},
      "zona": {"type": "string"}, **PAGE}, None),
    ("", ["POST"], False, "Crear el club del representante (rol club_admin)", None, CLUB_EXAMPLE),
    ("/deportes", ["GET"], True, "Catálogo de deportes", None, None),
    ("/me", ["GET"], False, "El club que administro (activa un correo asignado)", None, None),
    ("/announcements", ["GET"], True, "Sección de convocatorias abiertas",
     {"deporte": {"type": "string"}, "deporte_id": {"type": "integer"}, "categoria": {"type": "string"},
      "edad": {"type": "integer"}, "club_id": {"type": "string", "format": "uuid"},
      "fecha_desde": {"type": "string", "format": "date"}, "fecha_hasta": {"type": "string", "format": "date"},
      "estado": {"type": "string", "enum": ["ABIERTA", "CERRADA", "FINALIZADA"], "default": "ABIERTA"}, **PAGE}, None),
    ("/announcements/applications/me", ["GET"], False, "Mis postulaciones y su estado",
     {"estado": {"type": "string", "enum": ["PENDIENTE", "PRESELECCIONADO", "ACEPTADO", "RECHAZADO"]}, **PAGE}, None),
    ("/announcements/applications/{postulacion_id}", ["PATCH"], False, "Cambiar el estado de un candidato (club dueño)",
     None, {"estado": "PRESELECCIONADO", "comentario": "Te esperamos el sábado."}),
    ("/announcements/{convocatoria_id}", ["GET"], True, "Detalle de una convocatoria", None, None),
    ("/announcements/{convocatoria_id}/applications", ["GET"], False, "Candidatos de una convocatoria (club dueño)",
     {"estado": {"type": "string", "enum": ["PENDIENTE", "PRESELECCIONADO", "ACEPTADO", "RECHAZADO"]}, **PAGE}, None),
    ("/announcements/{convocatoria_id}/applications", ["POST"], False, "Postular a una convocatoria (deportista)",
     None, {"mensaje": "Juego de lateral.", "antecedentes": []}),
    ("/notices/me", ["GET"], False, "Mi bandeja de comunicados de clubes",
     {"club_id": {"type": "string", "format": "uuid"}, "prioridad": {"type": "string", "enum": ["NORMAL", "URGENTE"]}, **PAGE}, None),
    ("/consultas/me", ["GET"], False, "Mis conversaciones con clubes (deportista)", None, None),
    ("/{club_id}", ["GET"], True, "Perfil público de un club", None, None),
    ("/{club_id}", ["PATCH"], False, "Editar el perfil del club (dueño)", None,
     {"descripcion": "Nueva descripción", "deporte_ids": [1]}),
    ("/{club_id}/deportes/{deporte_id}", ["PUT", "DELETE"], False, "Agregar o quitar un deporte del club", None, None),
    ("/{club_id}/announcements", ["POST"], False, "Publicar una convocatoria (club dueño)", None, CONVOCATORIA_EXAMPLE),
    ("/{club_id}/announcements/{convocatoria_id}/close", ["POST"], False, "Cerrar una convocatoria (club dueño)", None, None),
    ("/{club_id}/notices", ["GET"], False, "Comunicados que publicó el club (dueño)",
     {"audiencia": {"type": "string", "enum": ["TODOS", "POSTULANTES", "MIEMBROS"]},
      "prioridad": {"type": "string", "enum": ["NORMAL", "URGENTE"]}, **PAGE}, None),
    ("/{club_id}/notices", ["POST"], False, "Publicar un comunicado (club dueño)", None,
     {"titulo": "Cambio de horario", "contenido": "La prueba del sábado empieza a las 9:00.", "audiencia": "TODOS", "prioridad": "NORMAL"}),
    ("/{club_id}/admins", ["GET"], False, "Administradores del club", None, None),
    ("/{club_id}/admins/assignments", ["GET"], False, "Correos asignados como administradores (pendientes)", None, None),
    ("/{club_id}/admins/assignments", ["POST"], False, "Asignar un correo como administrador (principal)", None,
     {"email": "persona@club.cl"}),
    ("/{club_id}/admins/assignments/{asignacion_id}", ["DELETE"], False, "Revocar un correo pendiente (principal)",
     None, None),
    ("/{club_id}/admins/{usuario_id}", ["DELETE"], False, "Quitar a un administrador o salir del club", None, None),
    # "me" va antes que {hilo_id} para que la documentación no confunda las dos rutas.
    ("/{club_id}/consultas/me/messages", ["GET"], False, "Mi conversación con el club (deportista)", MESSAGES, None),
    ("/{club_id}/consultas/me/messages", ["POST"], False, "Escribirle al club (deportista)", None, MESSAGE_EXAMPLE),
    ("/{club_id}/consultas/me/read", ["POST"], False, "Marcar como leídas las respuestas del club", None, {"hasta_id": 42}),
    ("/{club_id}/consultas", ["GET"], False, "Bandeja de consultas del club (administradores)", None, None),
    ("/{club_id}/consultas/{hilo_id}/messages", ["GET"], False, "Mensajes de una consulta (administradores)", MESSAGES, None),
    ("/{club_id}/consultas/{hilo_id}/messages", ["POST"], False, "Responder una consulta (administradores)", None, MESSAGE_EXAMPLE),
    ("/{club_id}/consultas/{hilo_id}/read", ["POST"], False, "Marcar como leída una consulta", None, {"hasta_id": 42}),
]

PAYMENTS = [
    ("/plans", ["GET"], True, "Planes disponibles", {"tipo": {"type": "string", "enum": ["USUARIO", "CLUB"]}}, None),
    ("/webpay/initiate", ["POST"], False, "Iniciar el pago de un plan con Webpay Plus", None, {"plan_id": 1}),
    ("/webpay/commit", ["POST"], False, "Confirmar el pago al volver de Webpay", None, {"token_ws": "a" * 64}),
    ("/subscription/me", ["GET"], False, "Mi suscripción", None, None),
    # The browser comes back from Webpay here (no token): the service redirects it to the app.
    ("/webpay/return", ["GET", "POST"], True, "Retorno de Webpay Plus (redirige a la app)", None, None),
    # Simulated Webpay for development: the service answers 404 to these unless WEBPAY_MOCK_MODE=true.
    ("/webpay/mock", ["GET", "POST"], True, "Página de pago simulada (solo con WEBPAY_MOCK_MODE)", None, None),
    ("/webpay/mock/decision", ["GET"], True, "Decisión en la página simulada (solo con WEBPAY_MOCK_MODE)", None, None),
]


async def proxy(request: Request, service: Annotated[ClubsService, Depends(get_clubs_service)]):
    return await forward_request(request, service)


def register(target: APIRouter, routes, prefix_name: str) -> None:
    for path, methods, public, summary, query, body in routes:
        for method in methods:
            parameters = [
                {"name": name, "in": "path", "required": True,
                 "schema": {"type": "integer"} if name in INTEGER_PATH_PARAMETERS else {"type": "string", "format": "uuid"}}
                for name in PATH_PARAMETERS if "{" + name + "}" in path
            ]
            parameters += [{"name": name, "in": "query", "required": False, "schema": schema}
                           for name, schema in (query or {}).items()]
            docs = {"parameters": parameters}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                docs["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"type": "object"}, "example": body}}}
            target.add_api_route(
                path, proxy, methods=[method], response_class=Response, summary=summary,
                dependencies=[] if public else [Depends(get_current_principal)],
                name=f"{prefix_name}_{method.lower()}_{path.strip('/').replace('/', '_') or 'root'}",
                openapi_extra=docs,
            )


register(router, CLUBS, "clubs")
register(payments_router, PAYMENTS, "payments")
