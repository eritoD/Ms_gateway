"""Unit tests for HealthService."""

from app.core.config import Settings
from app.services.health_service import HealthService


def test_health_service_reports_liveness_and_readiness() -> None:
    service = HealthService(Settings(service_name="test-gateway"))

    liveness = service.get_liveness()
    readiness = service.get_readiness()

    assert liveness.service == "test-gateway"
    assert liveness.status == "ok"
    assert readiness.service == "test-gateway"
    assert readiness.status == "ready"
