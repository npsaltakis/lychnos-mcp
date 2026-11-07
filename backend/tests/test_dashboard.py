from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.passwords import PasswordStore, hash_password, verify_password
from app.main import create_app
from tests.conftest import TOKEN
from tests.fakes import FakeProvider

PASSWORD = "correct horse battery"
ORIGIN = {"Origin": "http://127.0.0.1:3000"}


@pytest.fixture
def web(settings: Settings, provider: FakeProvider):
    PasswordStore(settings.password_path).set(PASSWORD)
    with TestClient(create_app(settings, providers=[provider]), base_url="http://127.0.0.1:3000") as client:
        yield client


def login(client: TestClient, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"password": password}, headers=ORIGIN)


def test_password_hashing() -> None:
    record = hash_password("12345678")
    assert verify_password("12345678", record)
    assert not verify_password("12345679", record)
    assert "12345678" not in str(record)
    with pytest.raises(ValueError):
        hash_password("short")


def test_requires_login_without_token(web: TestClient) -> None:
    assert web.get("/api/devices").status_code == 401
    assert web.get("/api/auth/status").json() == {"authenticated": False, "password_set": True}


def test_login_sets_httponly_strict_cookie(web: TestClient) -> None:
    response = login(web)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert web.get("/api/devices").status_code == 200
    assert web.get("/api/auth/status").json()["authenticated"] is True

    web.post("/api/auth/logout", headers=ORIGIN)
    assert web.get("/api/devices").status_code == 401


def test_wrong_password_and_lockout(web: TestClient) -> None:
    for _ in range(5):
        assert login(web, "wrong-password").status_code == 401
    locked = login(web)  # even the right password is refused while locked out
    assert locked.status_code == 429
    assert locked.json()["error"]["details"]["retry_after_s"] > 0


def test_login_rejected_from_foreign_origin(web: TestClient) -> None:
    response = web.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 401
    assert web.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401  # no Origin


def test_cookie_changes_need_same_origin(web: TestClient) -> None:
    login(web)
    assert web.post("/api/discovery/scan", headers={"Origin": "https://evil.example"}).status_code == 401
    assert web.post("/api/discovery/scan").status_code == 401  # missing Origin
    assert web.post("/api/discovery/scan", headers=ORIGIN).status_code == 200


def test_bearer_token_still_works_for_programs(web: TestClient) -> None:
    assert web.get("/api/devices", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert web.post("/api/discovery/scan", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_websocket_with_session_cookie(web: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect("/api/ws", subprotocols=["shc.v1"], headers=ORIGIN) as ws:
            ws.receive_json()
    login(web)
    # The test client does not attach its cookie jar to WebSockets; send it like a browser does.
    cookie = {"Cookie": f"shc_session={web.cookies['shc_session']}"}
    with web.websocket_connect("/api/ws", subprotocols=["shc.v1"], headers={**ORIGIN, **cookie}) as ws:
        assert ws.receive_json()["type"] == "snapshot"
    with pytest.raises(WebSocketDisconnect):
        with web.websocket_connect(
            "/api/ws", subprotocols=["shc.v1"], headers={"Origin": "https://evil.example", **cookie}
        ) as ws:
            ws.receive_json()


def test_no_password_means_no_login(settings: Settings, provider: FakeProvider) -> None:
    with TestClient(create_app(settings, providers=[provider]), base_url="http://127.0.0.1:3000") as client:
        assert client.get("/api/auth/status").json()["password_set"] is False
        assert login(client).status_code == 409


def test_dashboard_served_with_spa_fallback(settings: Settings, provider: FakeProvider, tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>dash</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    settings.frontend_dist = dist
    with TestClient(create_app(settings, providers=[provider])) as client:
        assert client.get("/").text == "<html>dash</html>"
        assert client.get("/scenes").text == "<html>dash</html>"
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert "nope" not in client.get("/../secret.txt").text
        assert client.get("/api/unknown").status_code in (401, 404)
