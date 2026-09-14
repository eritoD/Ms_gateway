"""HTTP communication with the users service; the gateway never opens its database."""

import httpx
from fastapi import HTTPException


class UsersService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def forward(self, method: str, path: str, *, query: bytes = b"",
                      content: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
        # Build the URL from our configured origin and a known users route.
        url = self.client.base_url.copy_with(path=path, query=query)
        try:
            return await self.client.request(method, url, content=content, headers=headers)
        except httpx.TimeoutException as error:
            raise HTTPException(status_code=504, detail="Ms_Users no respondió a tiempo") from error
        except httpx.RequestError as error:
            raise HTTPException(status_code=503, detail="Ms_Users no está disponible") from error

    async def check_ready(self) -> None:
        try:
            response = await self.forward("GET", "/api/v1/users/health/ready")
            if response.status_code == 200 and response.json().get("status") == "ready":
                return
        except (HTTPException, ValueError, AttributeError):
            pass
        raise HTTPException(status_code=503, detail="Ms_Users o su base de datos no están disponibles")
