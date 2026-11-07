"""Change log: changes made outside Lychnos (Google Home, vendor app, switch) and availability."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.domain.models import RGB, DeviceState, LightMode
from app.services.device_service import describe_changes
from tests.conftest import AUTH, register_all
from tests.fakes import FakeProvider


def _activity(client: TestClient, kind: str | None = None) -> list[dict]:
    url = "/api/activity?limit=50" + (f"&kind={kind}" if kind else "")
    return client.get(url, headers=AUTH).json()


def _wait(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_describe_changes() -> None:
    old = DeviceState(power=True, brightness=50, color=RGB(r=255, g=0, b=0), mode=LightMode.COLOR)
    new = DeviceState(power=True, brightness=80, color_temp_k=3000, mode=LightMode.WHITE)
    assert describe_changes(old, new) == [("brightness", "brightness 80%"), ("color_temp", "white 3000K")]
    # values seen for the first time (after startup) are not changes
    assert describe_changes(DeviceState(), new) == []


def test_external_change_is_logged(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)  # first read: not logged
    assert _activity(client, "changes") == []

    # Someone switches it on and makes it green from Google Home
    light = provider.lights["aa:01"]
    light.state = light.state.model_copy(update={"power": True, "color": RGB(r=0, g=255, b=0)})
    client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)

    assert _wait(lambda: len(_activity(client, "changes")) == 2)
    entries = _activity(client, "changes")
    assert {e["command"] for e in entries} == {"ON", "color #00ff00"}
    assert all(e["source"] == "external" and e["outcome"] == "observed" for e in entries)


def test_own_commands_are_not_logged_twice(client: TestClient) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_color", "color": {"r": 1, "g": 2, "b": 250}}, headers=AUTH)
    time.sleep(0.2)
    assert _activity(client, "changes") == []  # the colour (and implicit power-on) came from us
    commands = _activity(client, "commands")
    assert commands[0]["command"] == "color #0102fa" and commands[0]["outcome"] == "confirmed"


def test_availability_is_logged(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)
    provider.lights["aa:01"].online = False
    for _ in range(2):  # offline_after_failures = 2 in the test settings
        client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)
    provider.lights["aa:01"].online = True
    client.get(f"/api/devices/{desk}/state?refresh=true", headers=AUTH)
    assert _wait(lambda: [e["command"] for e in _activity(client, "availability")] == ["online", "offline"])
    assert all(e["source"] == "system" for e in _activity(client, "availability"))
