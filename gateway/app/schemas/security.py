"""Response schemas for security diagnostic routes."""

from pydantic import BaseModel


class PrincipalResponse(BaseModel):
    subject: str
    roles: list[str]
    scopes: list[str]


class AuthorizationCheckResponse(BaseModel):
    authorized: bool
    subject: str
