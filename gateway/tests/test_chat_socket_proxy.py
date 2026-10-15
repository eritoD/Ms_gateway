"""The chat WebSocket through the gateway, against a fake Ms_Matching that records what it receives."""
import asyncio
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

import app.api.routes.chat_socket as chat_socket
from app.core.config import get_settings
from app.main import create_app
from tests.test_users_proxy import SECRET, token_headers

WS = "/api/v1/chats/ws"


class FakeMatching:
    """A WebSocket server that answers like Ms_Matching and notes every connection."""

    def __init__(self):
        self.connections = 0
        self.received = []
        self.handshakes = []
        self.close_codes = []
        self.port = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _handler(self, ws):
        self.connections += 1
        self.handshakes.append({name.lower(): value for name, value in ws.request.headers.raw_items()})
        try:
            async for message in ws:
                self.received.append(message)
                frame = json.loads(message)
                kind = frame.get("type")
                if kind == "auth":
                    await ws.send(json.dumps({"type": "ready"}))
                elif kind == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                elif kind == "close":
                    await ws.close(code=frame["code"])
                elif kind == "burst":
                    for number in range(frame["count"]):
                        await ws.send(json.dumps({"type": "n", "n": number}))
                else:
                    await ws.send(json.dumps({"type": "echo", "frame": frame}))
        finally:
            self.close_codes.append(ws.close_code)

    def _run(self):
        asyncio.set_event_loop(self._loop)

        async def start():
            self._server = await serve(self._handler, "127.0.0.1", 0)
            self.port = next(iter(self._server.sockets)).getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    def start(self):
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def stop(self):
        async def shutdown():
            self._server.close()
            await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


def wait_until(condition, seconds=3):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        time.sleep(0.02)
    return condition()


def access_token(user=None, minutes=10):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user or uuid4()), "iss": "sportmatch-auth", "aud": "sportmatch-mobile", "iat": now,
                       "exp": now + timedelta(minutes=minutes), "token_type": "access", "roles": ["player"]},
                      SECRET, algorithm="HS256")


@contextmanager
def gateway(monkeypatch, matching=None, *, port=None, origins=""):
    monkeypatch.delenv("MATCHING_SERVICE_URL", raising=False)
    if matching is not None or port is not None:
        monkeypatch.setenv("MATCHING_SERVICE_URL", f"http://127.0.0.1:{port or matching.port}")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        get_settings.cache_clear()


@pytest.fixture
def matching():
    server = FakeMatching().start()
    yield server
    server.stop()


def auth_frame(token=None):
    return {"type": "auth", "token": token or access_token()}


def closed_with(ws):
    with pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    return closed.value.code


# -- relaying ---------------------------------------------------------------------------------------------


def test_a_valid_first_frame_opens_the_service_and_everything_flows_both_ways(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            frame = auth_frame()
            ws.send_json(frame)
            assert ws.receive_json() == {"type": "ready"}  # the service answered
            assert json.loads(matching.received[0]) == frame  # it got the very same frame
            for number in range(5):
                ws.send_json({"type": "join", "conversationId": str(number)})
                assert ws.receive_json() == {"type": "echo", "frame": {"type": "join", "conversationId": str(number)}}
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        assert wait_until(lambda: matching.close_codes)  # the client left: the service connection closed too
    assert matching.connections == 1


def test_events_from_the_service_arrive_in_order(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame())
            ws.receive_json()
            ws.send_json({"type": "burst", "count": 200})
            assert [ws.receive_json()["n"] for _ in range(200)] == list(range(200))


def test_people_do_not_share_a_service_connection(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as first, client.websocket_connect(WS) as second:
            first.send_json(auth_frame())
            second.send_json(auth_frame())
            assert first.receive_json() == second.receive_json() == {"type": "ready"}
            first.send_json({"type": "x", "who": "first"})
            second.send_json({"type": "x", "who": "second"})
            assert first.receive_json()["frame"]["who"] == "first"
            assert second.receive_json()["frame"]["who"] == "second"
    assert matching.connections == 2


def test_nothing_of_the_client_but_a_request_id_reaches_the_service(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS, headers={"Cookie": "session=secret", "Authorization": "Bearer robado",
                                                   "X-User-ID": "admin", "X-Forwarded-For": "6.6.6.6"}) as ws:
            ws.send_json(auth_frame())
            ws.receive_json()
    [handshake] = matching.handshakes
    for forged in ("cookie", "authorization", "x-user-id", "x-forwarded-for"):
        assert forged not in handshake
    assert handshake["x-request-id"]


# -- who gets in ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "first",
    [
        {"type": "auth", "token": "no-es-un-jwt"},
        {"type": "auth"},
        {"type": "auth", "token": 123},
        {"type": "ping"},
        {"type": "join", "conversationId": str(uuid4())},
        ["auth"],
        "texto",
    ],
)
def test_a_bad_first_frame_never_reaches_the_service(monkeypatch, matching, first):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(first)
            assert closed_with(ws) == 4401
    assert matching.connections == 0


def test_expired_foreign_and_non_access_tokens_are_refused_at_the_gateway(monkeypatch, matching):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(uuid4()), "iss": "sportmatch-auth", "aud": "sportmatch-mobile", "iat": now,
              "exp": now + timedelta(minutes=5), "token_type": "access"}
    bad = [
        access_token(minutes=-5),
        jwt.encode({**claims, "token_type": "refresh"}, SECRET, algorithm="HS256"),
        jwt.encode({**claims, "aud": "otra-app"}, SECRET, algorithm="HS256"),
        jwt.encode(claims, "otra-clave-de-mas-de-32-bytes-0123456789-abcdef", algorithm="HS256"),
    ]
    with gateway(monkeypatch, matching) as client:
        for token in bad:
            with client.websocket_connect(WS) as ws:
                ws.send_json(auth_frame(token))
                assert closed_with(ws) == 4401
    assert matching.connections == 0


def test_an_oversized_first_frame_is_refused(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "x" * 5000}))
            assert closed_with(ws) == 4401
    assert matching.connections == 0


def test_not_authenticating_in_time_closes_without_touching_the_service(monkeypatch, matching):
    monkeypatch.setattr(chat_socket, "AUTH_TIMEOUT_SECONDS", 0.3)
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            assert closed_with(ws) == 4401
    assert matching.connections == 0


def test_a_website_that_is_not_allowed_is_refused_but_the_app_and_the_site_get_in(monkeypatch, matching):
    site = "https://sportmatch.example"
    with gateway(monkeypatch, matching, origins=site) as client:
        with pytest.raises(Exception):  # the handshake itself is refused
            with client.websocket_connect(WS, headers={"Origin": "https://evil.example"}):
                pass
        assert matching.connections == 0
        for headers in ({"Origin": site}, {}):  # the website, and the mobile app (which sends no Origin)
            with client.websocket_connect(WS, headers=headers) as ws:
                ws.send_json(auth_frame())
                assert ws.receive_json() == {"type": "ready"}
    assert matching.connections == 2


def test_without_a_configured_service_the_socket_is_refused(monkeypatch):
    with gateway(monkeypatch, None) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(WS):
                pass


# -- when the service closes or fails ---------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [4401, 4409, 1008, 1009, 1013])
def test_the_close_code_of_the_service_reaches_the_client(monkeypatch, matching, code):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame())
            ws.receive_json()
            ws.send_json({"type": "close", "code": code})
            assert closed_with(ws) == code


def test_an_unreachable_service_closes_with_1013(monkeypatch):
    unused = FakeMatching().start()
    port = unused.port
    unused.stop()  # nothing listens there any more
    with gateway(monkeypatch, port=port) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame())
            assert closed_with(ws) == 1013


def test_the_client_cannot_send_binary_or_huge_frames_through(monkeypatch, matching):
    with gateway(monkeypatch, matching) as client:
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame())
            ws.receive_json()
            ws.send_bytes(b"\x00\x01")
            assert closed_with(ws) == 1003
        with client.websocket_connect(WS) as ws:
            ws.send_json(auth_frame())
            ws.receive_json()
            ws.send_text(json.dumps({"type": "x", "pad": "y" * 5000}))
            assert closed_with(ws) == 1009
    assert all(len(frame) <= 4096 for frame in matching.received)  # neither reached the service
