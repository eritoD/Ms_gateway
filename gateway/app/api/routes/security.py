"""Diagnostic endpoints demonstrating JWT and role/scope enforcement."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.security import (
    get_current_principal,
    require_any_role,
    require_scopes,
)
from app.schemas.security import AuthorizationCheckResponse, PrincipalResponse
from app.services.token_service import (
    Principal,
)

router = APIRouter(prefix="/api/v1/security", tags=["Security"])


@router.get(
    "/me",
    response_model=PrincipalResponse,
    summary="Inspeccionar la identidad JWT validada",
    responses={401: {"description": "JWT ausente o inválido"}},
)
async def read_current_principal(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PrincipalResponse:
    return PrincipalResponse(
        subject=principal.subject,
        roles=sorted(principal.roles),
        scopes=sorted(principal.scopes),
    )


@router.get(
    "/admin-check",
    response_model=AuthorizationCheckResponse,
    summary="Comprobar un rol y un scope de administración",
    responses={
        401: {"description": "JWT ausente o inválido"},
        403: {"description": "Rol o scope insuficiente"},
    },
)
async def check_admin_authorization(
    principal: Annotated[Principal, Depends(require_any_role("admin"))],
    _scoped_principal: Annotated[
        Principal,
        Depends(require_scopes("gateway:read")),
    ],
) -> AuthorizationCheckResponse:
    return AuthorizationCheckResponse(authorized=True, subject=principal.subject)
