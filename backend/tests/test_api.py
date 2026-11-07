from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from tests.conftest import AUTH, TOKEN, register_all
from tests.fakes import FakeProvider


# ----------------------------------------------------------------------------- security


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_api_requires_token(client: TestClient) -> None:
    assert client.get("/api/devices").status_code == 401
    bad = client.get("/api/devices", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/devices", headers=AUTH).status_code == 200


def test_untrusted_host_header_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert response.status_code == 400


def test_refuses_public_bind_without_opt_in(tmp_path) -> None:
    s = Settings(_env_file=None, data_dir=tmp_path, host="0.0.0.0")
    with pytest.raises(ValueError, match="SHC_ALLOW_REMOTE"):
        s.validate_exposure()
    Settings(_env_file=None, data_dir=tmp_path, host="0.0.0.0", allow_remote=True).validate_exposure()


def test_token_file_created(tmp_path) -> None:
    from app.core.security import load_or_create_token

    path = tmp_path / "tok"
    first = load_or_create_token(path)
    assert len(first) >= 32
    assert load_or_create_token(path) == first


# ----------------------------------------------------------------------------- devices


def test_discovered_devices_start_unregistered_and_cannot_be_controlled(client: TestClient) -> None:
    found = client.post("/api/discovery/scan", headers=AUTH).json()
    assert {v["device"]["name"] for v in found} == {"Desk Bar", "Plug Lamp"}
    assert all(not v["device"]["registered"] for v in found)

    device_id = found[0]["device"]["id"]
    response = client.post(
        f"/api/devices/{device_id}/commands", json={"type": "set_power", "on": True}, headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_not_registered"


def test_confirmed_command_updates_state(client: TestClient) -> None:
    ids = register_all(client)
    response = client.post(
        f"/api/devices/{ids['Desk Bar']}/commands",
        json={"type": "set_color", "color": {"r": 0, "g": 0, "b": 255}},
        headers=AUTH,
    )
    body = response.json()
    assert response.status_code == 200
    assert body["outcome"] == "confirmed"
    assert body["state"]["color"] == {"r": 0, "g": 0, "b": 255}

    view = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()
    assert view["runtime"]["availability"] == "online"
    assert view["runtime"]["pending"] is None
    assert view["runtime"]["state"]["color"] == {"r": 0, "g": 0, "b": 255}


def test_device_without_readback_is_unconfirmed(client: TestClient) -> None:
    ids = register_all(client)
    body = client.post(
        f"/api/devices/{ids['Plug Lamp']}/commands", json={"type": "set_power", "on": True}, headers=AUTH
    ).json()
    assert body["outcome"] == "unconfirmed"
    assert body["state"]["power"] is None  # nothing is claimed that the device did not report


def test_mismatch_is_reported(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    provider.lights["aa:01"].ignore_commands = True
    body = client.post(
        f"/api/devices/{ids['Desk Bar']}/commands", json={"type": "set_power", "on": True}, headers=AUTH
    ).json()
    assert body["outcome"] == "mismatch"
    assert body["state"]["power"] is False


def test_unsupported_capability_rejected(client: TestClient) -> None:
    ids = register_all(client)
    response = client.post(
        f"/api/devices/{ids['Plug Lamp']}/commands",
        json={"type": "set_brightness", "brightness": 40},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capability_not_supported"


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ({"type": "set_color_temperature", "kelvin": 1500}, "invalid_command"),
        ({"type": "set_brightness", "brightness": 0}, "invalid_command"),
        ({"type": "set_brightness", "brightness": 101}, "validation_error"),
        ({"type": "fly"}, "validation_error"),
    ],
)
def test_out_of_range_values_rejected(client: TestClient, command: dict, code: str) -> None:
    ids = register_all(client)
    response = client.post(f"/api/devices/{ids['Desk Bar']}/commands", json=command, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_offline_device_fails_and_becomes_offline(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    provider.lights["aa:01"].online = False
    for _ in range(2):
        response = client.post(
            f"/api/devices/{ids['Desk Bar']}/commands", json={"type": "set_power", "on": True}, headers=AUTH
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "device_offline"
        assert response.json()["error"]["details"]["result"]["outcome"] == "failed"
    view = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()
    assert view["runtime"]["availability"] == "offline"


def test_timeout_reported(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    provider.lights["aa:01"].delay_s = 2
    response = client.post(
        f"/api/devices/{ids['Desk Bar']}/commands", json={"type": "set_power", "on": True}, headers=AUTH
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "device_timeout"


def test_group_command_skips_unsupported(client: TestClient) -> None:
    ids = register_all(client)
    body = client.post(
        "/api/groups/all/commands", json={"type": "set_color", "color": {"r": 128, "g": 0, "b": 128}}, headers=AUTH
    ).json()
    assert [r["device_id"] for r in body["results"]] == [ids["Desk Bar"]]
    assert [s["device_id"] for s in body["skipped"]] == [ids["Plug Lamp"]]
    assert body["outcome"] == "all_confirmed"

    off = client.post("/api/groups/all/commands", json={"type": "set_power", "on": False}, headers=AUTH).json()
    assert off["outcome"] == "partial"  # Plug Lamp cannot confirm
    assert len(off["results"]) == 2


def test_state_refresh_uses_cache(client: TestClient, provider: FakeProvider) -> None:
    ids = register_all(client)
    client.get(f"/api/devices/{ids['Desk Bar']}/state?refresh=true", headers=AUTH)
    calls = provider.get_state_calls
    client.get(f"/api/devices/{ids['Desk Bar']}/state", headers=AUTH)
    assert provider.get_state_calls == calls  # fresh cache, no network call


def test_rename_and_delete(client: TestClient) -> None:
    ids = register_all(client)
    renamed = client.patch(f"/api/devices/{ids['Desk Bar']}", json={"name": "Γραφείο"}, headers=AUTH).json()
    assert renamed["device"]["name"] == "Γραφείο"
    assert client.delete(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).status_code == 204
    assert client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).status_code == 404


# ----------------------------------------------------------------------------- scenes


def test_scene_lifecycle(client: TestClient) -> None:
    ids = register_all(client)
    scene = client.post(
        "/api/scenes",
        json={
            "name": "Movie night",
            "actions": [
                {"device_id": ids["Desk Bar"], "color": {"r": 60, "g": 0, "b": 120}, "brightness": 20},
                {"device_id": ids["Plug Lamp"], "power": False},
            ],
        },
        headers=AUTH,
    )
    assert scene.status_code == 201
    scene_id = scene.json()["id"]

    dup = client.post(
        "/api/scenes",
        json={"name": "movie NIGHT", "actions": [{"device_id": ids["Plug Lamp"], "power": True}]},
        headers=AUTH,
    )
    assert dup.status_code == 409

    result = client.post(f"/api/scenes/{scene_id}/activate", headers=AUTH).json()
    by_device = {}
    for r in result["results"]:
        by_device.setdefault(r["device_id"], []).append(r["outcome"])
    assert by_device[ids["Desk Bar"]] == ["confirmed", "confirmed", "confirmed"]
    assert by_device[ids["Plug Lamp"]] == ["unconfirmed"]
    assert result["outcome"] == "partial"

    state = client.get(f"/api/devices/{ids['Desk Bar']}", headers=AUTH).json()["runtime"]["state"]
    assert state["power"] is True and state["brightness"] == 20


def test_scene_with_unsupported_action_rejected(client: TestClient) -> None:
    ids = register_all(client)
    response = client.post(
        "/api/scenes",
        json={"name": "Bad", "actions": [{"device_id": ids["Plug Lamp"], "brightness": 30}]},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["problems"][0]["device_id"] == ids["Plug Lamp"]


def test_scene_action_validation() -> None:
    from pydantic import ValidationError

    from app.domain.models import SceneAction

    with pytest.raises(ValidationError):
        SceneAction(device_id="x", power=False, brightness=10)
    with pytest.raises(ValidationError):
        SceneAction(device_id="x", color={"r": 1, "g": 2, "b": 3}, color_temp_k=3000)
    with pytest.raises(ValidationError):
        SceneAction(device_id="x")


# ----------------------------------------------------------------------------- websocket


def test_websocket_requires_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", subprotocols=["shc.v1"]) as ws:
            ws.receive_json()


def test_websocket_snapshot_and_events(client: TestClient) -> None:
    ids = register_all(client)
    with client.websocket_connect("/api/ws", subprotocols=["shc.v1", f"bearer.{TOKEN}"]) as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert len(snapshot["data"]["devices"]) == 2

        client.post(
            f"/api/devices/{ids['Desk Bar']}/commands", json={"type": "set_power", "on": True}, headers=AUTH
        )
        types = []
        while "command_result" not in types:
            event = ws.receive_json()
            types.append(event["type"])
        # pending is published before the result, so clients can show "in progress"
        assert types[0] == "device_runtime"
        assert event["data"]["outcome"] == "confirmed"
