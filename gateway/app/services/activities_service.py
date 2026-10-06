from app.services.users_service import UsersService


class ActivitiesService(UsersService):
    """Gateway proxy only; business rules belong to Ms_Activities."""
    name = "Ms_Activities"
    ready_path = "/api/v1/activities/health/ready"
