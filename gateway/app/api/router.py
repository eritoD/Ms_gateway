"""Main API router for the gateway."""

from fastapi import APIRouter

from app.api.routes.gateway import router as gateway_router
from app.api.routes.health import router as health_router
from app.api.routes.security import router as security_router
from app.api.routes.users import router as users_router
from app.api.routes.matching import router as matching_router

api_router = APIRouter()
api_router.include_router(gateway_router)
api_router.include_router(health_router)
api_router.include_router(security_router)
api_router.include_router(users_router)
api_router.include_router(matching_router)
