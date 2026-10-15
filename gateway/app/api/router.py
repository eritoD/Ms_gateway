"""Main API router for the gateway."""

from fastapi import APIRouter

from app.api.routes.activities import ratings_router, router as activities_router
from app.api.routes.chat_socket import router as chat_socket_router
from app.api.routes.clubs import payments_router, router as clubs_router
from app.api.routes.gateway import router as gateway_router
from app.api.routes.health import router as health_router
from app.api.routes.security import router as security_router
from app.api.routes.users import router as users_router
from app.api.routes.matching import chats_router, router as matching_router
from app.api.routes.notifications import router as notifications_router

api_router = APIRouter()
api_router.include_router(gateway_router)
api_router.include_router(health_router)
api_router.include_router(security_router)
api_router.include_router(users_router)
api_router.include_router(matching_router)
api_router.include_router(chats_router)
api_router.include_router(chat_socket_router)
api_router.include_router(clubs_router)
api_router.include_router(payments_router)

api_router.include_router(activities_router)
api_router.include_router(ratings_router)
api_router.include_router(notifications_router)
