"""HU-45: restricts the access of suspended accounts at the edge, on every authenticated request.

JWTs are stateless and live up to 30 minutes, so a suspension cannot wait for the token to expire. Each
authenticated request (HTTP and the chat WebSocket) asks Ms_Users whether the account is suspended, through an
internal endpoint that is protected by a shared secret and is never declared in this gateway:

    GET {users}/internal/v1/accounts/{id}/status      header X-Internal-Service-Token

Answers are cached for a few seconds per account (`ttl`), so Ms_Users is not asked on every request. The gateway
drops the cache when it relays a successful apply/lift of a sanction, so on that gateway the change is immediate;
another replica would see it within `ttl`.

If Ms_Users does not answer: the last known answer is used while it is at most `stale_seconds` old (the lifetime of
a token); with no usable answer the request fails with 503. Nothing is let through just because the check failed.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from uuid import UUID

import httpx

log = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 3.0
MAX_ENTRIES = 10_000


class AccountSuspendedError(Exception):
    """403 account_suspended. Only what the person may see: type, reason and end date."""

    def __init__(self, tipo: str, motivo: str, fin: str | None) -> None:
        super().__init__("account suspended")
        self.tipo, self.motivo, self.fin = tipo, motivo, fin

    def body(self) -> dict:
        if self.tipo == "permanente" or not self.fin:
            detail = f"Tu cuenta fue suspendida de forma permanente. Motivo: {self.motivo}"
        else:
            detail = f"Tu cuenta está suspendida hasta el {self.fin[8:10]}-{self.fin[5:7]}-{self.fin[:4]}. Motivo: {self.motivo}"
        return {"detail": detail, "code": "account_suspended", "tipo": self.tipo, "motivo": self.motivo, "fin": self.fin}


class AccountStatusUnavailable(Exception):
    """Ms_Users could not tell and there is no usable earlier answer."""


class ClosedAccountGuard:
    """Stands in for the guard when the suspension check is required but cannot be performed.

    Used when Ms_Users is configured and INTERNAL_SERVICE_TOKEN is not: nobody can be checked, so nobody who needs
    the check is let through (503 on HTTP, 1013 on the chat socket). Admins are never checked and keep working.
    """

    ttl = 15.0

    def invalidate(self) -> None:
        """Nothing is cached."""

    async def check(self, subject: str) -> None:
        raise AccountStatusUnavailable


class AccountGuard:
    def __init__(
        self, client: httpx.AsyncClient, token: str, *, ttl: float = 15, stale_seconds: float = 1800,
        clock=time.monotonic,
    ) -> None:
        self._client, self._token = client, token
        self.ttl, self._stale, self._clock = ttl, stale_seconds, clock
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def invalidate(self) -> None:
        """Forget every answer (called after a sanction is applied or lifted through this gateway)."""
        self._cache.clear()

    async def check(self, subject: str) -> None:
        try:
            UUID(subject)
        except ValueError:
            return  # not an account id: Ms_Users will refuse such a token on its own
        status = await self._status(subject)
        if status.get("suspended"):
            raise AccountSuspendedError(str(status.get("tipo")), str(status.get("motivo")), status.get("fin"))

    async def _status(self, subject: str) -> dict:
        now = self._clock()
        cached = self._cache.get(subject)
        if cached and now - cached[0] < self.ttl:
            return cached[1]
        try:
            url = self._client.base_url.copy_with(path=f"/internal/v1/accounts/{subject}/status", query=b"")
            response = await self._client.request(
                "GET", url, headers={"X-Internal-Service-Token": self._token}, timeout=CHECK_TIMEOUT_SECONDS)
            data = response.json() if response.status_code == 200 else None
            if not isinstance(data, dict) or not isinstance(data.get("suspended"), bool):
                raise ValueError("unexpected answer")
        except (httpx.HTTPError, ValueError):
            if cached and now - cached[0] < self._stale:
                log.warning("Ms_Users did not answer the account check; using the last known answer")
                return cached[1]
            raise AccountStatusUnavailable from None
        self._cache[subject] = (now, data)
        self._cache.move_to_end(subject)
        while len(self._cache) > MAX_ENTRIES:
            self._cache.popitem(last=False)
        return data
