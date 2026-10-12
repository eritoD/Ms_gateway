from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.dependencies.security import get_current_principal
from app.core.config import get_settings
from app.services.token_service import Principal

router = APIRouter(prefix='/api/v1/notifications', tags=['Notifications'])


class RevokeDevice(BaseModel):
    device_id: UUID
    revoke_secret: UUID


class RegisterDevice(RevokeDevice):
    token: str = Field(pattern=r'^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]$', max_length=250)
    consent: Literal[True]


class ReadNotifications(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=100)


def repository(request: Request):
    repo = getattr(request.app.state, 'notifications', None)
    if repo is None:
        raise HTTPException(503, 'Las notificaciones aún no están configuradas.')
    return repo


def actor(principal: Annotated[Principal, Depends(get_current_principal)]):
    try:
        return UUID(principal.subject)
    except ValueError:
        raise HTTPException(401, 'Sesión inválida.') from None


@router.put('/devices', status_code=204)
def register(body: RegisterDevice, user_id=Depends(actor), repo=Depends(repository), settings=Depends(get_settings)):
    if not settings.push_enabled:
        raise HTTPException(503, 'Las alertas al celular todavía no están habilitadas. Puedes consultar tus avisos en la app.')
    repo.register(user_id, body)
    return Response(status_code=204)


@router.post('/devices/revoke', status_code=204)
def revoke(body: RevokeDevice, repo=Depends(repository)):
    """Revocation capability only: cannot read, subscribe, or choose a recipient."""
    repo.revoke(body.device_id, body.revoke_secret)
    return Response(status_code=204)


@router.get('')
def inbox(before: UUID | None = None, limit: int = Query(50, ge=1, le=100), user_id=Depends(actor), repo=Depends(repository)):
    return repo.inbox(user_id, before, limit)


@router.post('/read', status_code=204)
def read(body: ReadNotifications, user_id=Depends(actor), repo=Depends(repository)):
    repo.mark_read(user_id, body.ids)
    return Response(status_code=204)
