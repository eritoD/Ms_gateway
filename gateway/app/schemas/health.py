"""Response schemas for health routes."""

from typing import Literal

from pydantic import BaseModel


class LiveHealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ReadyHealthResponse(BaseModel):
    status: Literal["ready"]
    service: str
