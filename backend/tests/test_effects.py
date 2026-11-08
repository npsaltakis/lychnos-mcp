"""Effects: run steps on colour lights, stop on request or when the light gets another command."""

import asyncio

from fastapi.testclient import TestClient

from app.services import effect_service as effect_module
from tests.conftest import AUTH, register_all


def test_rainbow_runs_and_stops_on_other_command(client: TestClient, monkeypatch) -> None:
    async def fast(_s: float) -> None:
        await asyncio.sleep(0.01)

    monkeypatch.setattr(effect_module, "_sleep", fast)
    ids = register_all(client)
    desk = ids["Desk Bar"]
    started = client.post("/api/effects", json={"effect": "rainbow", "target": {"kind": "all"}}, headers=AUTH)
    assert started.status_code == 200 and started.json()["device_ids"] == [desk]  # the plug has no colour

    async def wait() -> None:
        await asyncio.sleep(0.2)

    client.portal.call(wait)
    first = client.get(f"/api/devices/{desk}", headers=AUTH).json()["runtime"]["state"]
    assert first["power"] is True and first["color"] is not None
    assert client.get("/api/effects", headers=AUTH).json()[0]["effect"] == "rainbow"

    # Any other command takes the light back
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_brightness", "brightness": 40}, headers=AUTH)
    client.portal.call(wait)
    assert client.get("/api/effects", headers=AUTH).json() == []
    log = client.get("/api/activity?limit=500", headers=AUTH).json()
    assert not any(e["source"] == "effect" for e in log)  # steps are not logged
    assert any(e["command"] == "effect rainbow" for e in log)


def test_stop_and_validation(client: TestClient, monkeypatch) -> None:
    async def fast(_s: float) -> None:
        await asyncio.sleep(0.01)

    monkeypatch.setattr(effect_module, "_sleep", fast)
    ids = register_all(client)
    plug = ids["Plug Lamp"]
    bad = client.post("/api/effects", json={"effect": "candle", "target": {"kind": "device", "id": plug}}, headers=AUTH)
    assert bad.status_code == 422
    client.post("/api/effects", json={"effect": "breathe", "target": {"kind": "all"}}, headers=AUTH)
    assert client.post("/api/effects/stop", json={}, headers=AUTH).json() == []


def test_scene_can_start_an_effect(client: TestClient, monkeypatch) -> None:
    async def fast(_s: float) -> None:
        await asyncio.sleep(0.01)

    monkeypatch.setattr(effect_module, "_sleep", fast)
    ids = register_all(client)
    desk, plug = ids["Desk Bar"], ids["Plug Lamp"]
    bad = client.post("/api/scenes", json={"name": "x", "actions": [{"device_id": plug, "effect": "candle"}]}, headers=AUTH)
    assert bad.status_code == 422
    both = {"name": "y", "actions": [{"device_id": desk, "effect": "candle", "music_style": "rhythm"}]}
    assert client.post("/api/scenes", json=both, headers=AUTH).status_code == 422
    scene = client.post("/api/scenes", json={"name": "Πάρτι", "actions": [
        {"device_id": desk, "brightness": 60, "effect": "rainbow"}]}, headers=AUTH).json()
    client.post(f"/api/scenes/{scene['id']}/activate", headers=AUTH)
    assert client.get("/api/effects", headers=AUTH).json()[0]["effect"] == "rainbow"
    client.post("/api/effects/stop", json={}, headers=AUTH)
