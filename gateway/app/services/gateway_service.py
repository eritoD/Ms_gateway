"""Service containing gateway information behavior."""

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    service: str
    version: str
    status: Literal["running"] = "running"


class GatewayService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_status(self) -> GatewayStatus:
        return GatewayStatus(
            service=self._settings.service_name,
            version=self._settings.app_version,
        )

    def get_probe_message(self) -> str:
        return "El gateway de SportMatch funciona correctamente"
