"""Chat WebSocket through the gateway: `wss://<gateway>/api/v1/chats/ws` (app and website).

The protocol is the one of Ms_Matching (see its README): the first frame is
`{"type": "auth", "token": "<access token>"}` because a browser cannot send headers on a WebSocket.
The gateway checks that token itself, at the edge, and opens the connection to Ms_Matching only if
it is valid, so someone who never authenticates costs the chat service nothing. After that it is a
transparent relay in both directions; Ms_Matching still authenticates the same frame, decides who
sees which event and closes the socket when the token expires. Whatever code the service closes
with (4401 expired, 4409 replaced, 1008 too many frames, 1013 unavailable...) reaches the client.

Not forwarded to the service: cookies, Authorization and every other client header (only a request
id travels), so the service sees nothing the client could have forged.
"""

import asyncio
import json
import logging
from uuid import uuid4

import jwt
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, InvalidURI, WebSocketException

from app.core.config import get_settings
from app.services.token_service import SecurityConfigurationError, TokenService

log = logging.getLogger(__name__)
router = APIRouter()

AUTH_TIMEOUT_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 5
MAX_FRAME_CHARACTERS = 4096
UNAUTHORIZED, UNAVAILABLE, TOO_BIG, NOT_TEXT = 4401, 1013, 1009, 1003
# Codes a server may put on the wire; 1005/1006 mean "no code" and cannot be sent.
_RESERVED = {1005, 1006, 1015}


def upstream_url(base_url: str) -> str:
    """ws(s)://host/api/v1/chats/ws from the configured http(s) origin of Ms_Matching."""
    scheme = "wss" if base_url.startswith("https") else "ws"
    return f"{scheme}://{base_url.split('://', 1)[1].rstrip('/')}/api/v1/chats/ws"


async def _close(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.close(code)
    except RuntimeError:  # already closed
        pass


async def _first_frame(websocket: WebSocket, settings) -> str | None:
    """The auth frame, exactly as received, if it carries a valid access token."""
    try:
        text = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS)
        frame = json.loads(text) if len(text) <= MAX_FRAME_CHARACTERS else None
        if not isinstance(frame, dict) or frame.get("type") != "auth" or not isinstance(frame.get("token"), str):
            return None
        TokenService(settings).decode_access_token(frame["token"])
        return text
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, ValueError, jwt.PyJWTError):
        return None


async def _to_upstream(websocket: WebSocket, upstream) -> int | None:
    """Client -> service. Returns the code to close the client with, or None if the client left."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        if text is None:
            return NOT_TEXT
        if len(text) > MAX_FRAME_CHARACTERS:
            return TOO_BIG
        await upstream.send(text)


async def _to_client(websocket: WebSocket, upstream) -> None:
    """Service -> client, until the service closes."""
    async for frame in upstream:
        if isinstance(frame, str):
            await websocket.send_text(frame)


@router.websocket("/api/v1/chats/ws")
async def chat_socket(websocket: WebSocket) -> None:
    settings = get_settings()
    origin = websocket.headers.get("origin")
    # Defence in depth for browsers: the token travels in a frame, not in a cookie, so a foreign page
    # cannot use someone's session, but an unexpected website has no business here either.
    if origin and settings.cors_origin_list and origin not in settings.cors_origin_list:
        await websocket.close(4403)  # before accepting: the handshake is refused
        return
    client = getattr(websocket.app.state, "matching_client", None)
    if client is None:
        await websocket.close(UNAVAILABLE)
        return

    await websocket.accept()
    try:
        first = await _first_frame(websocket, settings)
    except SecurityConfigurationError:
        await _close(websocket, UNAVAILABLE)
        return
    if first is None:
        await _close(websocket, UNAUTHORIZED)
        return

    try:
        upstream = await websockets.connect(
            upstream_url(str(client.base_url)), open_timeout=CONNECT_TIMEOUT_SECONDS,
            additional_headers={"X-Request-ID": uuid4().hex}, max_size=2 * MAX_FRAME_CHARACTERS + 4096,
            user_agent_header=None,
        )
    except (OSError, asyncio.TimeoutError, WebSocketException, InvalidURI) as error:
        log.warning("Ms_Matching socket unavailable (%s)", type(error).__name__)
        await _close(websocket, UNAVAILABLE)
        return

    close_code: int | None = 1000
    try:
        await upstream.send(first)
        relays = [asyncio.create_task(_to_upstream(websocket, upstream)), asyncio.create_task(_to_client(websocket, upstream))]
        done, pending = await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if relays[0] in done and not relays[0].cancelled() and relays[0].exception() is None:
            close_code = relays[0].result()  # None: the client left; otherwise our own reason (1009, 1003)
        else:
            code = upstream.close_code
            close_code = code if code is not None and code not in _RESERVED else 1011
    except (ConnectionClosed, WebSocketDisconnect, RuntimeError):
        code = upstream.close_code
        close_code = code if code is not None and code not in _RESERVED else 1011
    finally:
        await upstream.close()
        if close_code is not None:
            await _close(websocket, close_code)
