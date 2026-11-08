from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import smarthome_mcp.server as server
from smarthome_mcp.client import BackendClient, BackendError
from smarthome_mcp.resolve import is_all, parse_color, resolve_device

CAPS = {
    "supported": ["power", "brightness", "color_rgb", "color_temp"],
    "brightness_min": 1,
    "color_temp_range": {"min_k": 2000, "max_k": 9000},
    "supports_state_query": True,
    "supports_push": False,
}
STATE = {"power": True, "brightness": 100, "color": {"r": 255, "g": 127, "b": 0}, "color_temp_k": None, "mode": "color"}


def view(id_: str, name: str, manufacturer: str, registered: bool = True) -> dict:
    return {
        "device": {
            "id": id_, "provider": id_.split("-")[0], "native_id": "x", "name": name,
            "manufacturer": manufacturer, "model": "H6056", "address": "1.2.3.4",
            "registered": registered, "capabilities": CAPS, "metadata": {},
        },
        "runtime": {
            "availability": "online", "state": STATE, "state_confirmed_at": None,
            "pending": None, "last_error": None, "consecutive_failures": 0, "last_seen_at": None,
        },
    }


VIEWS = [
    view("govee_lan-aa", "Govee RGBICWW Light Bars", "Govee"),
    view("ewelink_lan-bb", "Υπνοδωμάτιο", "Sonoff"),
    view("ewelink_lan-cc", "Spare bulb", "Sonoff", registered=False),
]


# ----------------------------------------------------------------------------- resolution


def test_resolve_by_manufacturer_name_and_greek() -> None:
    assert resolve_device(VIEWS, "the Govee light")["device"]["id"] == "govee_lan-aa"
    assert resolve_device(VIEWS, "light bars")["device"]["id"] == "govee_lan-aa"
    assert resolve_device(VIEWS, "υπνοδωματιο")["device"]["id"] == "ewelink_lan-bb"  # no accents
    assert resolve_device(VIEWS, "Sonoff")["device"]["id"] == "ewelink_lan-bb"  # unregistered one ignored


def test_resolve_errors() -> None:
    with pytest.raises(BackendError, match="device_not_found|No device"):
        resolve_device(VIEWS, "Philips")
    with pytest.raises(BackendError) as exc:
        resolve_device(VIEWS, "spare bulb")
    assert exc.value.code == "device_not_registered"
    two = VIEWS + [view("govee_lan-dd", "Govee Lamp", "Govee")]
    with pytest.raises(BackendError) as exc:
        resolve_device(two, "Govee")
    assert exc.value.code == "ambiguous_device"


def test_colors() -> None:
    assert parse_color("blue") == {"r": 0, "g": 60, "b": 255}
    assert parse_color("Μωβ") == parse_color("purple")
    assert parse_color("#102030") == {"r": 16, "g": 32, "b": 48}
    assert parse_color("rgb(1, 2, 3)") == {"r": 1, "g": 2, "b": 3}
    with pytest.raises(BackendError):
        parse_color("blurple")
    assert is_all("all") and is_all("Όλα") and not is_all("Govee")


# ----------------------------------------------------------------------------- tools against a fake backend


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.command_status = 200
        self.error_code = "device_offline"
        self.outcome = "confirmed"
        self.rooms: list[dict] = [{"id": "r1", "name": "Σαλόνι", "icon": "sofa", "sort_order": 0, "created_at": ""}]
        self.created_automation: dict | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"] == "Bearer t0ken"
        if path == "/api/devices":
            return httpx.Response(200, json=VIEWS)
        if path.endswith("/commands") and path.startswith("/api/devices/"):
            device_id = path.split("/")[3]
            result = {
                "command_id": "c1", "device_id": device_id, "command": body, "outcome": self.outcome,
                "state": STATE, "availability": "online", "error": None, "duration_ms": 170,
            }
            if self.command_status != 200:
                return httpx.Response(
                    self.command_status,
                    json={"error": {"code": self.error_code, "message": "Govee did not answer", "details": {"result": result}}},
                )
            return httpx.Response(200, json=result)
        if path == "/api/groups/all/commands":
            return httpx.Response(200, json={"outcome": "partial", "results": [], "skipped": [{"device_id": "ewelink_lan-bb", "reason": "no colour"}]})
        if path == "/api/scenes" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "s1", "name": "Ταινία", "description": "", "actions": [], "created_at": "", "updated_at": ""}])
        if path == "/api/scenes" and request.method == "POST":
            return httpx.Response(201, json={**body, "id": "s2", "created_at": "", "updated_at": ""})
        if path == "/api/scenes/s1/activate":
            return httpx.Response(200, json={"outcome": "all_confirmed", "results": [], "skipped": []})
        if path == "/api/rooms":
            return httpx.Response(200, json=self.rooms)
        if path.startswith("/api/rooms/") and path.endswith("/commands"):
            return httpx.Response(200, json={"outcome": "all_confirmed", "results": [], "skipped": []})
        if path == "/api/automations" and request.method == "POST":
            self.created_automation = body
            return httpx.Response(201, json={**body, "id": "a1", "enabled": True, "next_run_at": "2026-11-09T15:20:00Z",
                                             "last_run_at": None, "last_result": None})
        if path == "/api/automations":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(server, "_client", BackendClient(base_url="http://test", token="t0ken", transport=httpx.MockTransport(fake.handler)))
    return fake


async def test_set_color_by_name(backend: FakeBackend) -> None:
    result = await server.set_color("Govee", "blue")
    assert backend.calls[-1] == ("POST", "/api/devices/govee_lan-aa/commands", {"type": "set_color", "color": {"r": 0, "g": 60, "b": 255}})
    assert result["outcome"] == "confirmed" and result["verified_by_device"] is True


async def test_unconfirmed_is_not_reported_as_verified(backend: FakeBackend) -> None:
    backend.outcome = "unconfirmed"
    result = await server.turn_on("Govee")
    assert result["verified_by_device"] is False
    assert "Do not claim" in result["note"]


async def test_failed_command_raises_tool_error(backend: FakeBackend) -> None:
    backend.command_status = 503  # device_offline: never delivered
    with pytest.raises(ToolError, match="NOT applied"):
        await server.turn_off("Govee")


async def test_timeout_is_reported_as_unknown_not_as_not_applied(backend: FakeBackend) -> None:
    backend.command_status = 504
    backend.error_code = "device_timeout"
    with pytest.raises(ToolError, match="may or may not have been applied") as exc:
        await server.turn_off("Govee")
    assert "NOT applied" not in str(exc.value)


async def test_offline_state_is_marked_last_known(backend: FakeBackend) -> None:
    VIEWS[0]["runtime"]["availability"] = "offline"
    try:
        result = await server.list_devices()
    finally:
        VIEWS[0]["runtime"]["availability"] = "online"
    assert result["devices"][0]["state_is_last_known_only"] is True
    assert result["devices"][1]["state_is_last_known_only"] is False


async def test_all_uses_group_endpoint(backend: FakeBackend) -> None:
    result = await server.set_color("all", "purple")
    assert backend.calls[-1][1] == "/api/groups/all/commands"
    assert result["target"] == "all" and result["skipped"][0]["device"] == "Υπνοδωμάτιο"


async def test_list_devices_hides_unregistered(backend: FakeBackend) -> None:
    result = await server.list_devices()
    assert [d["name"] for d in result["devices"]] == ["Govee RGBICWW Light Bars", "Υπνοδωμάτιο"]
    assert result["devices"][0]["state"]["color_hex"] == "#ff7f00"


async def test_scenes(backend: FakeBackend) -> None:
    assert (await server.activate_scene("ταινια"))["scene"] == "Ταινία"
    created = await server.create_scene(
        "Movie night",
        [server.SceneActionInput(device="Govee", color="purple", brightness=20), server.SceneActionInput(device="Sonoff", power=False)],
    )
    assert created["created"] is True
    sent = backend.calls[-1][2]
    assert sent["actions"][0]["color"] == {"r": 140, "g": 0, "b": 255}
    assert sent["actions"][1]["device_id"] == "ewelink_lan-bb"


async def test_room_names_work_as_targets(backend: FakeBackend) -> None:
    result = await server.turn_off("σαλονι")  # no accent, lower case
    assert backend.calls[-1][:2] == ("POST", "/api/rooms/r1/commands")
    assert result["target"] == "room Σαλόνι"
    # device names still win when they are not a room name
    await server.turn_on("Govee")
    assert backend.calls[-1][1] == "/api/devices/govee_lan-aa/commands"


async def test_create_automation_sunset_in_room(backend: FakeBackend) -> None:
    result = await server.create_automation(
        name="Evening", when="sunset", action="turn_on", target="living room Σαλόνι", offset_minutes=-15, days=["weekdays"]
    )
    sent = backend.created_automation
    assert sent["trigger"] == {"type": "sun", "event": "sunset", "offset_min": -15, "cloudy_earlier_min": 0, "days": [0, 1, 2, 3, 4]}
    assert sent["action"]["target"] == {"kind": "room", "id": "r1"}
    assert sent["action"]["command"] == {"type": "set_power", "on": True}
    assert result["created"] is True and result["when"] == "sunset -15 min"


async def test_create_wake_up_fade(backend: FakeBackend) -> None:
    await server.create_automation(
        name="Wake", when="07:00", action="wake_up_fade", target="Govee", brightness=80, kelvin=2700, fade_minutes=20
    )
    assert backend.created_automation["action"] == {
        "type": "fade", "target": {"kind": "device", "id": "govee_lan-aa"},
        "to_brightness": 80, "duration_min": 20, "color_temp_k": 2700,
    }


def test_parse_days() -> None:
    from smarthome_mcp.resolve import parse_days

    assert parse_days(None) == [0, 1, 2, 3, 4, 5, 6]
    assert parse_days(["Σαββατοκύριακο"]) == [5, 6]
    assert parse_days(["mon", "Παρασκευή"]) == [0, 4]
    with pytest.raises(BackendError):
        parse_days(["someday"])


async def test_backend_not_running() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    server._client = BackendClient(base_url="http://test", token="t", transport=httpx.MockTransport(refuse))
    try:
        with pytest.raises(ToolError, match="backend_not_running"):
            await server.list_devices()
    finally:
        server._client = None
