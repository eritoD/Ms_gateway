"""Payments, plans and subscriptions are forwarded exclusively to ms-pago."""
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from app.api.dependencies.services import get_payments_service
from app.api.routes.proxy_registry import register
from app.api.routes.users import forward_request
from app.services.payments_service import PaymentsService

router = APIRouter(prefix="/api/v1/payments", tags=["Payments"])

PLAN_EXAMPLE = {"nombre": "Pro Mensual", "tipo": "USUARIO", "categoria": "PLAN", "precio": 4990, "duracion_dias": 30,
                "beneficios": ["Actividades sin límite", "Filtros avanzados"], "descripcion": "Para quien entrena seguido"}

PAYMENTS = [
    ("/webpay/checkout", ["GET"], True, "Formulario POST hacia Webpay (enlace firmado)", None, None),
    ("/plans", ["GET"], True, "Planes disponibles", {"tipo": {"type": "string", "enum": ["USUARIO", "CLUB"]}}, None),
    ("/webpay/initiate", ["POST"], False, "Iniciar el pago de un plan con Webpay Plus", None, {"plan_id": 1}),
    ("/webpay/commit", ["POST"], False, "Confirmar el pago al volver de Webpay", None, {"token_ws": "a" * 64}),
    ("/subscription/me", ["GET"], False, "Mi suscripción", None, None),
    # HU-56: gestión de planes y convenios. El rol admin lo exige ms-pago.
    ("/admin/plans", ["GET"], False, "Planes y convenios con sus suscripciones vigentes (administrador)",
     {"estado": {"type": "string", "enum": ["activo", "inactivo", "todos"]},
      "tipo": {"type": "string", "enum": ["USUARIO", "CLUB"]},
      "categoria": {"type": "string", "enum": ["PLAN", "CONVENIO"]},
      "pagina": {"type": "integer", "minimum": 1, "default": 1},
      "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}, None),
    ("/admin/plans", ["POST"], False, "Crear un plan o convenio (administrador)", None, PLAN_EXAMPLE),
    ("/admin/plans/{plan_id}", ["PATCH"], False, "Editar un plan o convenio activo (administrador)", None,
     {"precio": 5990, "beneficios": ["Actividades sin límite"], "motivo": "Ajuste de temporada"}),
    ("/admin/plans/{plan_id}/deactivate", ["POST"], False, "Desactivar un plan o convenio (administrador)", None,
     {"motivo": "Se retira la oferta"}),
    ("/admin/plans/{plan_id}/reactivate", ["POST"], False, "Reactivar un plan o convenio desactivado (administrador)",
     None, {"motivo": "Vuelve la oferta"}),
    ("/admin/plans/{plan_id}/subscriptions", ["GET"], False, "Suscripciones de un plan o convenio (administrador)",
     {"estado": {"type": "string", "enum": ["vigente", "vencida", "todas"]},
      "pagina": {"type": "integer", "minimum": 1, "default": 1},
      "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}, None),
    # The browser comes back from Webpay here (no token): the service redirects it to the app.
    ("/webpay/return", ["GET", "POST"], True, "Retorno de Webpay Plus (redirige a la app)", None, None),
    # Simulated Webpay for development: the service answers 404 to these unless WEBPAY_MOCK_MODE=true.
    ("/webpay/mock", ["GET", "POST"], True, "Página de pago simulada (solo con WEBPAY_MOCK_MODE)", None, None),
    ("/webpay/mock/decision", ["GET"], True, "Decisión en la página simulada (solo con WEBPAY_MOCK_MODE)", None, None),
]

async def proxy(request: Request, service: Annotated[PaymentsService, Depends(get_payments_service)]):
    return await forward_request(request, service)

register(router, PAYMENTS, "payments", proxy, ("plan_id",), {"plan_id"})
