"""Response schemas for gateway information routes."""

from typing import Literal

from pydantic import BaseModel


class GatewayInfoResponse(BaseModel):
    service: str
    version: str
    status: Literal["running"]


class GatewayProbeResponse(BaseModel):
    message: str
