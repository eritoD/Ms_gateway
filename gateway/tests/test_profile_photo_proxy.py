"""HU-61: the gateway and the profile photo (upload needs a session, a suspended account is blocked, size limits)."""
from uuid import uuid4

import httpx
import pytest

from tests.test_account_guard import SERVICE_TOKEN, SUSPENDED, USER, Upstream, bearer
from tests.test_users_proxy import proxy_client

PHOTO_PATH = f"/api/v1/users/{USER}/profile/photo"
KEY = "0123456789abcdef0123456789abcdef.jpg"


def multipart(size: int):
    body = b"--b\r\nContent-Disposition: form-data; name=\"foto\"; filename=\"a.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" \
        + b"\xff\xd8\xff" + b"0" * size + b"\r\n--b--\r\n"
    return body, {"Content-Type": "multipart/form-data; boundary=b"}


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    upstream = Upstream()
    with proxy_client(monkeypatch, upstream) as client:
        yield client, upstream


def test_uploading_without_a_session_is_401_and_never_reaches_the_service(guarded):
    client, upstream = guarded
    body, headers = multipart(100)
    assert client.put(PHOTO_PATH, content=body, headers=headers).status_code == 401
    assert client.delete(PHOTO_PATH).status_code == 401
    assert upstream.other_calls == []


def test_the_upload_is_relayed_with_its_multipart_body_and_content_type(guarded):
    client, upstream = guarded
    body, headers = multipart(2000)
    response = client.put(PHOTO_PATH, content=body, headers={**headers, **bearer()})
    assert response.status_code == 200
    relayed = upstream.other_calls[-1]
    assert relayed.method == "PUT" and relayed.url.path == PHOTO_PATH
    assert relayed.content == body
    assert relayed.headers["content-type"] == "multipart/form-data; boundary=b"


def test_a_suspended_account_cannot_upload_or_delete_its_photo(guarded):
    client, upstream = guarded
    upstream.accounts[USER] = SUSPENDED
    body, headers = multipart(100)
    for response in (client.put(PHOTO_PATH, content=body, headers={**headers, **bearer()}),
                     client.delete(PHOTO_PATH, headers=bearer())):
        assert response.status_code == 403
        assert response.json()["code"] == "account_suspended"
    assert upstream.other_calls == []


def test_an_upload_over_the_photo_limit_is_413_but_a_normal_one_still_has_the_1_mib_cap(guarded, monkeypatch):
    client, upstream = guarded
    big, headers = multipart(7 * 1024 * 1024)
    assert client.put(PHOTO_PATH, content=big, headers={**headers, **bearer()}).status_code == 413
    # another route keeps the 1 MiB limit
    assert client.put(f"/api/v1/users/{USER}/profile", content=b"{" + b" " * 1_100_000 + b"}",
                      headers={"Content-Type": "application/json", **bearer()}).status_code == 413
    assert upstream.other_calls == []
    # just under the photo limit goes through
    ok, headers = multipart(5 * 1024 * 1024 + 1000)
    assert client.put(PHOTO_PATH, content=ok, headers={**headers, **bearer()}).status_code == 200


def test_a_photo_is_served_without_a_session_with_nosniff_and_its_own_cache_rules(monkeypatch):
    def upstream(request):
        assert request.url.path == f"/api/v1/users/photos/{KEY}"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"\xff\xd8\xffdata", headers={
            "content-type": "image/jpeg", "cache-control": "private, max-age=86400, immutable"})
    with proxy_client(monkeypatch, upstream) as client:
        response = client.get(f"/api/v1/users/photos/{KEY}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=86400, immutable"
    assert response.content == b"\xff\xd8\xffdata"


def test_a_missing_photo_is_404_and_not_cached(monkeypatch):
    with proxy_client(monkeypatch, lambda request: httpx.Response(404, json={"detail": "Foto no encontrada."})) as client:
        response = client.get(f"/api/v1/users/photos/{uuid4().hex}.png")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_only_get_is_open_for_a_photo(monkeypatch):
    with proxy_client(monkeypatch, lambda request: httpx.Response(200, json={})) as client:
        assert client.put(f"/api/v1/users/photos/{KEY}", headers=bearer()).status_code in (404, 405)
        assert client.delete(f"/api/v1/users/photos/{KEY}", headers=bearer()).status_code in (404, 405)
