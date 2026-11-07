"""Notifications: settings, secrets handling, every channel (simulated) and the event watcher."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH, register_all
from tests.fakes import FakeProvider

BASE = {
    "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": ""},
    "telegram": {"enabled": False, "chat_id": ""},
    "email": {"enabled": False, "smtp_host": "", "smtp_port": 587, "security": "starttls",
              "username": "", "sender": "", "recipient": ""},
    "webpush": {"enabled": False},
    "events": {"device_offline": True, "device_online": True, "automation_failed": True,
               "automation_ran": False, "command_failed": False},
}


def settings(**overrides) -> dict:
    body = json.loads(json.dumps(BASE))
    for key, value in overrides.items():
        if key == "secrets":
            body["secrets"] = value
        else:
            body[key].update(value)
    return body


class Outbox:
    """Captures outgoing HTTP calls (ntfy, Telegram) instead of sending them."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.telegram_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((str(request.url), body))
        if "api.telegram.org" in str(request.url):
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [
                    {"message": {"chat": {"id": 12345, "first_name": "Νίκος"}}}]})
            if not self.telegram_ok:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"id": "x"})


@pytest.fixture
def outbox(client: TestClient) -> Outbox:
    box = Outbox()
    service = client.app.state.notification_service
    service._http = httpx.AsyncClient(transport=httpx.MockTransport(box.handler))
    return box


def test_everything_is_off_by_default(client: TestClient) -> None:
    view = client.get("/api/notifications", headers=AUTH).json()
    assert not any(view[c]["enabled"] for c in ("ntfy", "telegram", "email", "webpush"))
    assert view["secrets_set"] == {"ntfy_token": False, "telegram_token": False, "smtp_password": False}
    assert client.post("/api/notifications/test", headers=AUTH).status_code == 422  # nothing enabled


def test_secrets_are_stored_but_never_returned(client: TestClient) -> None:
    bad = client.put("/api/notifications", json=settings(telegram={"enabled": True, "chat_id": "12345"}), headers=AUTH)
    assert bad.status_code == 422 and "Telegram" in bad.json()["error"]["message"]

    ok = client.put(
        "/api/notifications",
        json=settings(telegram={"enabled": True, "chat_id": "12345"}, secrets={"telegram_token": "123:SECRET"}),
        headers=AUTH,
    )
    assert ok.status_code == 200
    assert ok.json()["secrets_set"]["telegram_token"] is True
    assert "SECRET" not in ok.text
    assert "SECRET" not in client.get("/api/notifications", headers=AUTH).text

    cleared = client.put("/api/notifications", json=settings(secrets={"telegram_token": ""}), headers=AUTH).json()
    assert cleared["secrets_set"]["telegram_token"] is False


def test_ntfy_and_telegram_test_messages(client: TestClient, outbox: Outbox) -> None:
    client.put(
        "/api/notifications",
        json=settings(ntfy={"enabled": True, "topic": "lychnos-abc123"}, telegram={"enabled": True, "chat_id": "12345"},
                      secrets={"telegram_token": "123:SECRET"}),
        headers=AUTH,
    )
    report = client.post("/api/notifications/test", headers=AUTH).json()
    assert report == {"ntfy": "ok", "telegram": "ok"}
    ntfy_url, ntfy_body = next(r for r in outbox.requests if "ntfy.sh" in r[0])
    assert ntfy_body["topic"] == "lychnos-abc123" and "δοκιμή" in ntfy_body["title"]  # Greek kept intact
    tg_url, tg_body = next(r for r in outbox.requests if "telegram" in r[0])
    assert "/bot123:SECRET/sendMessage" in tg_url and tg_body["chat_id"] == "12345"

    outbox.telegram_ok = False
    report = client.post("/api/notifications/test", json={"channel": "telegram"}, headers=AUTH).json()
    assert "chat not found" in report["telegram"]


def test_telegram_chat_detection(client: TestClient, outbox: Outbox) -> None:
    chats = client.post("/api/notifications/telegram/chats", json={"token": "123:SECRET"}, headers=AUTH).json()
    assert chats == [{"id": "12345", "name": "Νίκος"}]


def test_email_channel(client: TestClient) -> None:
    sent: list = []

    class FakeSMTP:
        def __init__(self, cfg) -> None:
            self.cfg = cfg

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def login(self, user, password) -> None:
            sent.append(("login", user, password))

        def send_message(self, message) -> None:
            sent.append(("send", message["To"], message["Subject"]))

    client.app.state.notification_service._smtp_factory = FakeSMTP
    client.put(
        "/api/notifications",
        json=settings(email={"enabled": True, "smtp_host": "smtp.gmail.com", "username": "me@example.com",
                             "sender": "me@example.com", "recipient": "me@example.com"},
                      secrets={"smtp_password": "app-password"}),
        headers=AUTH,
    )
    assert client.post("/api/notifications/test", json={"channel": "email"}, headers=AUTH).json() == {"email": "ok"}
    assert ("login", "me@example.com", "app-password") in sent
    assert sent[-1][0] == "send" and sent[-1][1] == "me@example.com"


def test_webpush_subscription_and_delivery(client: TestClient) -> None:
    pushed: list[dict] = []
    service = client.app.state.notification_service
    service._webpush_fn = lambda **kwargs: pushed.append(kwargs)

    key = client.get("/api/notifications/webpush/key", headers=AUTH).json()["public_key"]
    assert len(key) == 87  # 65-byte uncompressed P-256 point, base64url without padding
    assert client.get("/api/notifications/webpush/key", headers=AUTH).json()["public_key"] == key  # stable

    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    view = client.post("/api/notifications/webpush/subscriptions", json=sub, headers=AUTH).json()
    assert view["webpush_devices"] == 1
    assert client.post("/api/notifications/test", json={"channel": "webpush"}, headers=AUTH).json() == {"webpush": "ok"}
    assert json.loads(pushed[0]["data"])["title"].startswith("Lychnos")
    view = client.post("/api/notifications/webpush/unsubscribe", json={"endpoint": sub["endpoint"]}, headers=AUTH).json()
    assert view["webpush_devices"] == 0


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_offline_and_back_online_are_notified_once(client: TestClient, provider: FakeProvider, outbox: Outbox) -> None:
    ids = register_all(client)
    client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)  # online
    client.put("/api/notifications", json=settings(ntfy={"enabled": True, "topic": "lychnos-abc123"}), headers=AUTH)

    provider.lights["aa:01"].online = False
    for _ in range(3):  # offline_after_failures = 2 in the test settings
        client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)
    assert _wait_for(lambda: any("εκτός δικτύου" in r[1].get("title", "") for r in outbox.requests))

    provider.lights["aa:01"].online = True
    client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)
    assert _wait_for(lambda: any("ξανά online" in r[1].get("title", "") for r in outbox.requests))
    offline_alerts = [r for r in outbox.requests if "εκτός δικτύου" in r[1].get("title", "")]
    assert len(offline_alerts) == 1


def test_failed_automation_is_notified(client: TestClient, provider: FakeProvider, outbox: Outbox) -> None:
    ids = register_all(client)
    client.put("/api/notifications", json=settings(ntfy={"enabled": True, "topic": "lychnos-abc123"}), headers=AUTH)
    automation = client.post("/api/automations", json={
        "name": "Βράδυ",
        "trigger": {"type": "time", "time": "21:00"},
        "action": {"type": "command", "target": {"kind": "device", "id": ids["Desk Bar"]},
                   "command": {"type": "set_power", "on": True}},
    }, headers=AUTH).json()
    provider.lights["aa:01"].online = False
    client.post(f"/api/automations/{automation['id']}/run", headers=AUTH)
    assert _wait_for(lambda: any("Βράδυ" in r[1].get("title", "") and "απέτυχε" in r[1]["title"] for r in outbox.requests))


def test_disabled_channels_send_nothing(client: TestClient, provider: FakeProvider, outbox: Outbox) -> None:
    ids = register_all(client)
    client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)
    provider.lights["aa:01"].online = False
    for _ in range(3):
        client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)
    time.sleep(0.2)
    assert outbox.requests == []
