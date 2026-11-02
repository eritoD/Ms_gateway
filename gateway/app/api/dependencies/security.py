"""HTTP authentication and authorization dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from app.api.dependencies.services import get_token_service
from app.services.account_guard import AccountStatusUnavailable
from app.services.token_service import (
    Principal,
    SecurityConfigurationError,
    TokenService,
)

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="JWTBearer",
    description="Access token emitido por el servicio Usuarios/Auth.",
)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas o ausentes",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        principal = token_service.decode_access_token(credentials.credentials)
    except SecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La validación JWT no está configurada",
        ) from exc
    except InvalidTokenError as exc:
        raise _unauthorized() from exc

    # HU-45: a suspended account is refused on every request, even with a token that is still valid.
    guard = getattr(request.app.state, "account_guard", None)
    if guard is not None and "admin" not in principal.roles:
        try:
            await guard.check(principal.subject)  # AccountSuspendedError -> 403 account_suspended
        except AccountStatusUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo verificar el estado de la cuenta. Inténtalo de nuevo.",
            ) from None
    return principal


def require_any_role(*required_roles: str):
    required = frozenset(role.strip() for role in required_roles if role.strip())
    if not required:
        raise ValueError("At least one role is required")

    async def authorize_role(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if principal.roles.isdisjoint(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Rol insuficiente",
            )
        return principal

    return authorize_role


def require_scopes(*required_scopes: str):
    required = frozenset(scope.strip() for scope in required_scopes if scope.strip())
    if not required:
        raise ValueError("At least one scope is required")

    async def authorize_scopes(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if not required.issubset(principal.scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permisos insuficientes",
            )
        return principal

    return authorize_scopes
