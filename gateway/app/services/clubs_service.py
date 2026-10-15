from app.services.users_service import UsersService


class ClubsService(UsersService):
    """Gateway proxy only; business rules belong to ms-club."""
    name = "Ms_Club"
    ready_path = "/api/v1/clubs/health/ready"
