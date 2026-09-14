"""Unit tests for GatewayService."""

from app.core.config import Settings
from app.services.gateway_service import GatewayService


def test_gateway_service_uses_application_settings() -> None:
    service = GatewayService(
        Settings(
            service_name="test-gateway",
            app_version="9.9.9",
        )
    )

    status = service.get_status()

    assert status.service == "test-gateway"
    assert status.version == "9.9.9"
    assert status.status == "running"


def test_gateway_service_exposes_probe_message() -> None:
    service = GatewayService(Settings())

    assert service.get_probe_message() == (
        "El gateway de SportMatch funciona correctamente"
    )
