"""Service containing liveness and readiness behavior."""

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class LiveStatus:
    service: str
    status: Literal["ok"] = "ok"


@dataclass(frozen=True, slots=True)
class ReadyStatus:
    service: str
    status: Literal["ready"] = "ready"


class HealthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_liveness(self) -> LiveStatus:
        return LiveStatus(service=self._settings.service_name)

    def get_readiness(self) -> ReadyStatus:
        # The HTTP readiness route also checks Ms_Users when it is configured.
        return ReadyStatus(service=self._settings.service_name)
