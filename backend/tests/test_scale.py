"""Bathroom scale (Eufy C20 / T9130) over Bluetooth: packet decoding, adoption, storage, API."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.services.events import Event, EventType
from app.services.scale_service import parse_advertisement
from tests.conftest import AUTH

ADDR = "BC:0F:B7:02:08:74"
NAME = "eufy T9130"
# Real advertisements captured while weighing 110.6 kg (company id 48223 stripped by bleak).
MEASURING = bytes.fromhex("0fb7020874006185f06a0100c82d00000000")
FINAL = bytes.fromhex("0fb7020874006a85f06a2580342b00000072110903a00000")
OFF = bytes.fromhex("0fb7020874008385f06a0d40000000000000")


def test_parse_real_packets() -> None:
    m = parse_advertisement(MEASURING)
    assert m is not None and m.kg == 117.2 and not m.final
    f = parse_advertisement(FINAL)
    assert f is not None and f.kg == 110.6 and f.final
    o = parse_advertisement(OFF)
    assert o is not None and o.kg == 0 and o.stepped_off and not o.final
    assert parse_advertisement(b"\x01\x02") is None


def _feed(client: TestClient, address: str, name: str, payload: bytes, now: datetime | None = None) -> None:
    service = client.app.state.scale_service

    async def run() -> None:
        service.on_advertisement(address, name, {48223: payload}, now)
        await service.flush()

    client.portal.call(run)


def test_weighing_is_adopted_stored_and_deduplicated(client: TestClient) -> None:
    _feed(client, "AA:BB:CC:DD:EE:FF", "Some headphones", FINAL)  # not a scale
    assert client.get("/api/scale", headers=AUTH).json()["latest"] is None

    t0 = datetime.now(timezone.utc)
    _feed(client, ADDR, NAME, MEASURING, t0)
    status = client.get("/api/scale", headers=AUTH).json()
    assert status["settings"]["address"] == ADDR and status["settings"]["name"] == NAME
    assert status["latest"] is None  # still measuring

    _feed(client, ADDR, NAME, FINAL, t0 + timedelta(seconds=9))
    _feed(client, ADDR, NAME, FINAL, t0 + timedelta(seconds=10))  # repeated packet, same weighing
    rows = client.get("/api/scale/weighings?days=30", headers=AUTH).json()
    assert [r["kg"] for r in rows] == [110.6]

    # another scale is ignored once one is adopted
    _feed(client, "11:22:33:44:55:66", "eufy T9147", FINAL, t0 + timedelta(hours=1))
    assert len(client.get("/api/scale/weighings", headers=AUTH).json()) == 1

    assert client.delete(f"/api/scale/weighings/{rows[0]['id']}", headers=AUTH).status_code == 204
    assert client.get("/api/scale", headers=AUTH).json()["latest"] is None
    assert client.delete(f"/api/scale/weighings/{rows[0]['id']}", headers=AUTH).status_code == 404


def test_settings_disable_and_forget(client: TestClient) -> None:
    body = client.put("/api/settings/scale", json={"enabled": False, "address": "", "name": ""}, headers=AUTH).json()
    assert body["bluetooth"] == "off" and body["settings"]["address"] == ""


def test_weight_notification_text(client: TestClient) -> None:
    service = client.app.state.notification_service
    event = Event(
        type=EventType.WEIGHT_RECORDED,
        data={
            "weighing": {"id": 2, "ts": "2026-11-08T08:00:00+00:00", "kg": 110.6},
            "previous": {"id": 1, "ts": "2026-11-01T08:00:00+00:00", "kg": 111.0},
        },
    )
    n, _ = service._translate(event)
    assert n is not None and n.title == "Ζύγιση: 110,6 kg"
    assert n.message.startswith("−0,4 kg")
