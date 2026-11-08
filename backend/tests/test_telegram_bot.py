"""Telegram commands: only the owner's chat is obeyed; on/off/brightness/scenes/status work."""

import json

import httpx
from fastapi.testclient import TestClient

from tests.conftest import AUTH, register_all


def _power(client: TestClient, device_id: str) -> bool | None:
    return client.get(f"/api/devices/{device_id}", headers=AUTH).json()["runtime"]["state"]["power"]


def test_text_commands(client: TestClient) -> None:
    ids = register_all(client)
    bot = client.app.state.telegram_bot
    desk = ids["Desk Bar"]

    reply, _ = client.portal.call(bot.handle_text, "/off")
    assert "Έσβησαν: όλα τα φώτα" in reply and _power(client, desk) is False
    reply, _ = client.portal.call(bot.handle_text, "Άναψε desk")
    assert "Desk Bar" in reply and _power(client, desk) is True
    reply, _ = client.portal.call(bot.handle_text, "30% desk bar")
    assert client.get(f"/api/devices/{desk}", headers=AUTH).json()["runtime"]["state"]["brightness"] == 30
    reply, _ = client.portal.call(bot.handle_text, "κατάσταση")
    assert "Desk Bar: αναμμένο 30%" in reply
    reply, _ = client.portal.call(bot.handle_text, "άναψε κουζίνα")
    assert "Δεν βρήκα" in reply
    reply, kb = client.portal.call(bot.handle_text, "καλημέρα")
    assert "Δεν το κατάλαβα" in reply and kb is not None

    client.post("/api/scenes", json={"name": "Ταινία", "actions": [
        {"device_id": desk, "power": True, "brightness": 15}]}, headers=AUTH)
    reply, _ = client.portal.call(bot.handle_text, "σκηνή ταινια")
    assert "Ταινία" in reply and reply.startswith("✅")
    reply, kb = client.portal.call(bot.handle_text, "/scenes")
    assert kb["inline_keyboard"][0][0]["text"] == "🎬 Ταινία"
    log = client.get("/api/activity?limit=50", headers=AUTH).json()
    assert any(e["source"].startswith("telegram") for e in log)


def test_poll_obeys_only_the_owner(client: TestClient) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.portal.call(client.app.state.telegram_bot.handle_text, "on")
    sent: list[tuple[str, dict]] = []
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "off"}},  # stranger
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/status"}},
        {"update_id": 12, "callback_query": {"id": "q1", "data": "off", "message": {"chat": {"id": 42}}}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": updates})
        sent.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {}})

    bot = client.app.state.telegram_bot
    bot._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.portal.call(bot.poll_once, "123:T", "42", 0)

    messages = [body for method, body in sent if method == "sendMessage"]
    assert len(messages) == 2 and all(m["chat_id"] == "42" for m in messages)
    assert "Desk Bar" in messages[0]["text"]
    assert _power(client, desk) is False  # only the owner's "off" button counted
    assert bot._offset == 13
