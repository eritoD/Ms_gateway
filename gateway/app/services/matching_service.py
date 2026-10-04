from app.services.users_service import UsersService


class MatchingService(UsersService):
    """Gateway proxy only; business rules belong to Ms_Matching."""
    name = "Ms_Matching"
    ready_path = "/api/v1/matching/health/ready"
