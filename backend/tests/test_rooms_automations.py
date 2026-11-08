from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.services.automation_service as automation_module
from app.domain.automations import Location, SunTrigger, TimeTrigger
from app.services.automation_service import next_run
from app.services.sun import sun_event
from tests.conftest import AUTH, register_all
from tests.fakes import FakeLight, FakeProvider

ATHENS = Location(latitude=37.98, longitude=23.73, label="Athens")


# ============================================================================ sun & schedule maths


def test_sun_times_athens_midsummer() -> None:
    rise = sun_event(date(2026, 6, 21), ATHENS.latitude, ATHENS.longitude, "sunrise")
    sset = sun_event(date(2026, 6, 21), ATHENS.latitude, ATHENS.longitude, "sunset")
    assert rise is not None and sset is not None
    # Published values: about 03:02 UTC and 17:51 UTC
    assert abs((rise - datetime(2026, 6, 21, 3, 2, tzinfo=UTC)).total_seconds()) < 5 * 60
    assert abs((sset - datetime(2026, 6, 21, 17, 51, tzinfo=UTC)).total_seconds()) < 5 * 60


def test_polar_day_has_no_sunset() -> None:
    assert sun_event(date(2026, 6, 21), 80.0, 15.0, "sunset") is None


def test_next_run_time_trigger() -> None:
    trigger = TimeTrigger(time="07:30")
    morning = datetime(2026, 11, 9, 6, 0).astimezone()
    assert next_run(trigger, morning, None) == datetime(2026, 11, 9, 7, 30).astimezone()
    later = datetime(2026, 11, 9, 8, 0).astimezone()
    assert next_run(trigger, later, None) == datetime(2026, 11, 10, 7, 30).astimezone()


def test_next_run_respects_days() -> None:
    mondays = TimeTrigger(time="21:00", days=[0])
    friday = datetime(2026, 11, 13, 12, 0).astimezone()  # Friday
    assert next_run(mondays, friday, None) == datetime(2026, 11, 16, 21, 0).astimezone()


def test_next_run_sun_trigger_needs_location_and_applies_offset() -> None:
    trigger = SunTrigger(event="sunset", offset_min=-30)
    now = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    assert next_run(trigger, now, None) is None
    expected = sun_event(date(2026, 6, 21), ATHENS.latitude, ATHENS.longitude, "sunset") - timedelta(minutes=30)
    assert next_run(trigger, now, ATHENS) == expected


def test_invalid_trigger_values() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TimeTrigger(time="25:00")
    with pytest.raises(ValidationError):
        TimeTrigger(time="07:00", days=[7])


# ============================================================================ rooms


def test_room_lifecycle_and_room_commands(client: TestClient) -> None:
    ids = register_all(client)
    living = client.post("/api/rooms", json={"name": "Σαλόνι", "icon": "sofa"}, headers=AUTH)
    assert living.status_code == 201
    room_id = living.json()["id"]
    assert client.post("/api/rooms", json={"name": "σαλόνι"}, headers=AUTH).status_code == 409

    view = client.patch(f"/api/devices/{ids['Desk Bar']}", json={"room_id": room_id}, headers=AUTH).json()
    assert view["device"]["room_id"] == room_id

    batch = client.post(f"/api/rooms/{room_id}/commands", json={"type": "set_power", "on": True}, headers=AUTH).json()
    assert [r["device_id"] for r in batch["results"]] == [ids["Desk Bar"]]  # Plug Lamp is not in the room

    # Renaming a device keeps its room; null removes it from the room
    client.patch(f"/api/devices/{ids['Desk Bar']}", json={"name": "Μπάρα"}, headers=AUTH)
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["device"]["room_id"] == room_id
    client.patch(f"/api/devices/{ids['Desk Bar']}", json={"room_id": None}, headers=AUTH)
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["device"]["room_id"] is None

    # Deleting a room unassigns its devices
    client.patch(f"/api/devices/{ids['Desk Bar']}", json={"room_id": room_id}, headers=AUTH)
    assert client.delete(f"/api/rooms/{room_id}", headers=AUTH).status_code == 204
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["device"]["room_id"] is None
    assert client.patch(f"/api/devices/{ids['Desk Bar']}", json={"room_id": "nope"}, headers=AUTH).status_code == 404


# ============================================================================ history


def test_activity_records_source(client: TestClient) -> None:
    ids = register_all(client)
    client.post(
        f"/api/devices/{ids['Desk Bar']}/commands",
        json={"type": "set_brightness", "brightness": 40},
        headers={**AUTH, "X-SHC-Client": "claude"},
    )
    entries = client.get("/api/activity?limit=5", headers=AUTH).json()
    assert entries[0]["source"] == "claude"
    assert entries[0]["command"] == "brightness 40%"
    assert entries[0]["outcome"] == "confirmed"
    assert entries[0]["device_name"] == "Desk Bar"


# ============================================================================ automations


def _create(client: TestClient, body: dict) -> dict:
    response = client.post("/api/automations", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()


def test_automation_run_now_and_history(client: TestClient) -> None:
    ids = register_all(client)
    automation = _create(client, {
        "name": "Βράδυ",
        "trigger": {"type": "time", "time": "21:00"},
        "action": {"type": "command", "target": {"kind": "device", "id": ids["Desk Bar"]},
                   "command": {"type": "set_color", "color": {"r": 255, "g": 80, "b": 0}}},
    })
    assert automation["next_run_at"] is not None

    ran = client.post(f"/api/automations/{automation['id']}/run", headers=AUTH).json()
    assert ran["last_result"]["outcome"] == "all_confirmed"
    state = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]["state"]
    assert state["color"] == {"r": 255, "g": 80, "b": 0}
    assert client.get("/api/activity?limit=1", headers=AUTH).json()[0]["source"] == "automation · Βράδυ"

    disabled = client.patch(f"/api/automations/{automation['id']}", json={"enabled": False}, headers=AUTH).json()
    assert disabled["enabled"] is False and disabled["next_run_at"] is None
    assert client.delete(f"/api/automations/{automation['id']}", headers=AUTH).status_code == 204


def test_scheduler_tick_runs_due_automations(client: TestClient) -> None:
    ids = register_all(client)
    automation = _create(client, {
        "name": "Σβήσιμο",
        "trigger": {"type": "time", "time": "23:00"},
        "action": {"type": "command", "target": {"kind": "all"}, "command": {"type": "set_power", "on": True}},
    })
    service = client.app.state.automation_service
    due = datetime.fromisoformat(automation["next_run_at"])
    client.portal.call(service._tick, due - timedelta(seconds=5))  # not yet
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]["state"]["power"] is False
    client.portal.call(service._tick, due + timedelta(seconds=1))
    for _ in range(50):  # the run happens in a background task
        if service.get(automation["id"]).last_result:
            break
        client.portal.call(automation_module.asyncio.sleep, 0.02)
    assert service.get(automation["id"]).last_result["outcome"] == "partial"  # Plug Lamp cannot confirm
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]["state"]["power"] is True
    # rescheduled for the next day
    assert service.get(automation["id"]).next_run_at > due


def test_fade_wake_up_light(client: TestClient, monkeypatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(automation_module, "_sleep", instant)
    ids = register_all(client)
    automation = _create(client, {
        "name": "Ξύπνημα",
        "trigger": {"type": "time", "time": "07:00", "days": [0, 1, 2, 3, 4]},
        "action": {"type": "fade", "target": {"kind": "device", "id": ids["Desk Bar"]},
                   "to_brightness": 80, "duration_min": 1, "color_temp_k": 2700},
    })
    service = client.app.state.automation_service
    async def run() -> None:
        await service._run(service.get(automation["id"]), reason="test")

    client.portal.call(run)
    state = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]["state"]
    assert state["power"] is True and state["brightness"] == 80 and state["color_temp_k"] == 2700
    assert service.get(automation["id"]).last_result["outcome"] == "all_confirmed"


def test_fade_out_sleep_light(client: TestClient, monkeypatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(automation_module, "_sleep", instant)
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_power", "on": True}, headers=AUTH)
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_brightness", "brightness": 60}, headers=AUTH)
    automation = _create(client, {
        "name": "Ύπνος",
        "trigger": {"type": "time", "time": "23:30"},
        "action": {"type": "fade_out", "target": {"kind": "device", "id": desk}, "duration_min": 1},
    })
    service = client.app.state.automation_service

    async def run() -> None:
        await service._run(service.get(automation["id"]), reason="test")

    client.portal.call(run)
    state = client.get(f"/api/devices/{desk}", headers=AUTH).json()["runtime"]["state"]
    assert state["power"] is False
    assert service.get(automation["id"]).last_result["outcome"] == "all_confirmed"
    # Nothing on: nothing to do
    client.portal.call(run)
    assert service.get(automation["id"]).last_result["outcome"] == "nothing_to_do"


def test_sun_automation_requires_location(client: TestClient) -> None:
    body = {
        "name": "Δύση",
        "trigger": {"type": "sun", "event": "sunset", "offset_min": -15},
        "action": {"type": "command", "target": {"kind": "all"}, "command": {"type": "set_power", "on": True}},
    }
    response = client.post("/api/automations", json=body, headers=AUTH)
    assert response.status_code == 422 and "location" in response.json()["error"]["message"]
    client.put("/api/settings/location", json={"latitude": 37.98, "longitude": 23.73, "label": "Athens"}, headers=AUTH)
    created = _create(client, body)
    assert created["next_run_at"] is not None


def test_automation_validation(client: TestClient) -> None:
    ids = register_all(client)
    bad_scene = {"name": "x", "trigger": {"type": "time", "time": "08:00"}, "action": {"type": "scene", "scene_id": "nope"}}
    assert client.post("/api/automations", json=bad_scene, headers=AUTH).status_code == 404
    unsupported = {
        "name": "x",
        "trigger": {"type": "time", "time": "08:00"},
        "action": {"type": "command", "target": {"kind": "device", "id": ids["Plug Lamp"]},
                   "command": {"type": "set_brightness", "brightness": 50}},
    }
    assert client.post("/api/automations", json=unsupported, headers=AUTH).status_code == 422
