from app.services.users_service import UsersService


class PaymentsService(UsersService):
    """Proxy only: all payment rules and persistence belong to ms-pago."""
    name = "Ms_Pago"
    ready_path = "/api/v1/payments/health/ready"
