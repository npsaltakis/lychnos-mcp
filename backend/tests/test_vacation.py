"""Vacation mode: random on/off inside the evening window, everything it lit is switched off after."""

import random
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.conftest import AUTH, register_all


def _local(hh: int, mm: int = 0) -> datetime:
    now = datetime.now()
    return datetime(now.year, now.month, now.day, hh, mm).astimezone()


def _power(client: TestClient, device_id: str) -> bool | None:
    return client.get(f"/api/devices/{device_id}", headers=AUTH).json()["runtime"]["state"]["power"]


def test_vacation_settings_roundtrip(client: TestClient) -> None:
    status = client.get("/api/settings/vacation", headers=AUTH).json()
    assert status["settings"]["enabled"] is False and status["active"] is False
    body = {"enabled": True, "start": "time", "start_time": "19:00", "end_time": "23:30", "device_ids": []}
    status = client.put("/api/settings/vacation", json=body, headers=AUTH).json()
    assert status["settings"]["enabled"] is True and status["window_start"] is not None
    bad = {**body, "end_time": "25:00"}
    assert client.put("/api/settings/vacation", json=bad, headers=AUTH).status_code == 422


def test_vacation_switches_lights_and_cleans_up(client: TestClient) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    others = {i: _power(client, i) for name, i in ids.items() if i != desk}
    service = client.app.state.vacation_service
    service._rng = random.Random(1)
    body = {"enabled": True, "start": "time", "start_time": "19:00", "end_time": "23:00", "device_ids": [desk]}
    client.put("/api/settings/vacation", json=body, headers=AUTH)

    seen_on = []

    async def evening() -> None:
        t = _local(19, 0)
        while t < _local(22, 30):
            await service.tick(t)
            seen_on.append(desk in service._owned)
            t += timedelta(minutes=1)
        await service.tick(_local(23, 5))  # after the window

    client.portal.call(evening)
    assert any(seen_on) and not all(seen_on)  # it went on and off during the evening
    assert _power(client, desk) is False and not service._owned
    assert {i: _power(client, i) for i in others} == others  # other lights untouched
    log = client.get("/api/activity?limit=200", headers=AUTH).json()
    assert any(e["source"] == "vacation" for e in log)
