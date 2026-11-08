"""FastAPI application entry point for the SportMatch gateway."""

from contextlib import AsyncExitStack, asynccontextmanager
import asyncio
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.http_security import configure_http_security
from app.services.account_guard import AccountGuard, AccountSuspendedError, ClosedAccountGuard

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create the gateway application without external side effects."""

    settings = get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with AsyncExitStack() as stack:
            application.state.notifications = None
            if settings.notifications_database_url:
                from app.services.notifications import NotificationWorker
                worker = NotificationWorker(settings)
                await asyncio.to_thread(worker.start)
                stack.push_async_callback(asyncio.to_thread, worker.close)
                application.state.notifications = worker.repository
            for name, url in (("users", settings.users_service_url), ("matching", settings.matching_service_url), ("activities", settings.activities_service_url), ("clubs", settings.clubs_service_url), ("payments", settings.payments_service_url)):
                if url is not None:
                    client = await stack.enter_async_context(httpx.AsyncClient(
                        base_url=str(url), timeout=max(settings.users_timeout_seconds, 35) if name in {"clubs", "payments"} else settings.users_timeout_seconds,
                        follow_redirects=False, trust_env=False,
                    ))
                    setattr(application.state, f"{name}_client", client)
            application.state.account_guard = None
            users_client = getattr(application.state, "users_client", None)
            if users_client is not None and settings.internal_service_token is not None:
                application.state.account_guard = AccountGuard(
                    users_client, settings.internal_service_token.get_secret_value(),
                    ttl=settings.account_status_ttl_seconds, stale_seconds=settings.account_status_stale_seconds)
            elif users_client is not None:
                # Fail closed: without the secret nobody can be checked, so authenticated routes answer 503.
                application.state.account_guard = ClosedAccountGuard()
                log.error(
                    "INTERNAL_SERVICE_TOKEN is not set: the suspended-account check cannot run, so every "
                    "authenticated request and chat socket of non-admin users is REFUSED (503). Set the secret.")
            try:
                yield
            finally:
                application.state.account_guard = None
                application.state.users_client = None
                application.state.matching_client = None
                application.state.activities_client = None
                application.state.clubs_client = None
                application.state.payments_client = None
                application.state.notifications = None

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Punto de entrada inicial del backend SportMatch.",
        lifespan=lifespan,
    )
    configure_http_security(application, settings)

    @application.exception_handler(AccountSuspendedError)
    async def account_suspended(_: Request, error: AccountSuspendedError) -> JSONResponse:
        return JSONResponse(status_code=403, content=error.body(), headers={"Cache-Control": "no-store"})

    application.include_router(api_router)

    return application


app = create_app()
