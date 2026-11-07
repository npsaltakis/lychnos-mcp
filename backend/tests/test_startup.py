"""Startup robustness: broken providers and devices that are not reachable yet (e.g. Wi-Fi at logon)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

import app.services.device_service as device_service
from app.core.config import Settings
from app.main import create_app
from tests.conftest import AUTH, register_all
from tests.fakes import FakeProvider


class BrokenProvider(FakeProvider):
    key = "broken"
    manufacturer = "BrokenCo"

    async def start(self, sink) -> None:
        raise OSError("port 4002 in use")


def test_broken_provider_does_not_stop_backend(settings: Settings, provider: FakeProvider) -> None:
    app = create_app(settings, providers=[BrokenProvider([]), provider])
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert len(client.post("/api/discovery/scan", headers=AUTH).json()) == 2  # fake provider still works


def test_initial_refresh_retries_until_device_answers(settings: Settings, provider: FakeProvider, monkeypatch) -> None:
    monkeypatch.setattr(device_service, "INITIAL_REFRESH_DELAYS_S", (0, 0.1, 0.1, 0.1))
    # First run: register the device.
    with TestClient(create_app(settings, providers=[provider])) as client:
        ids = register_all(client)
    # Second run (like a reboot): the light is unreachable at first, then comes online.
    light = provider.lights["aa:01"]
    light.online = False
    with TestClient(create_app(settings, providers=[provider])) as client:
        time.sleep(0.05)
        light.online = True
        deadline = time.time() + 2
        state = None
        while time.time() < deadline:
            state = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]
            if state["availability"] == "online":
                break
            time.sleep(0.05)
        assert state is not None and state["availability"] == "online"
        assert state["state"]["power"] is False
