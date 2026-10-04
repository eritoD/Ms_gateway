"""FastAPI application entry point for the SportMatch gateway."""

from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.http_security import configure_http_security


def create_app() -> FastAPI:
    """Create the gateway application without external side effects."""

    settings = get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with AsyncExitStack() as stack:
            for name, url in (("users", settings.users_service_url), ("matching", settings.matching_service_url)):
                if url is not None:
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        base_url=str(url), timeout=settings.users_timeout_seconds,
                        follow_redirects=False, trust_env=False,
                    ))
                    setattr(application.state, f"{name}_client", client)
            try:
                yield
            finally:
                application.state.users_client = None
                application.state.matching_client = None

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Punto de entrada inicial del backend SportMatch.",
        lifespan=lifespan,
    )
    configure_http_security(application, settings)
    application.include_router(api_router)

    return application


app = create_app()
