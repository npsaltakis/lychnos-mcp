"""Bluetooth bathroom scales: packet decoding, discovery, adding, storage, API."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.services.events import Event, EventType
from app.services.scale_service import XIAOMI_V2_UUID, parse_eufy, parse_xiaomi_v1, parse_xiaomi_v2
from tests.conftest import AUTH

ADDR = "BC:0F:B7:02:08:74"
NAME = "eufy T9130"
# Real advertisements captured while weighing 110.6 kg (company id 48223 stripped by bleak).
MEASURING = bytes.fromhex("0fb7020874006185f06a0100c82d00000000")
FINAL = bytes.fromhex("0fb7020874006a85f06a2580342b00000072110903a00000")
OFF = bytes.fromhex("0fb7020874008385f06a0d40000000000000")


def test_parse_eufy_real_packets() -> None:
    m = parse_eufy(MEASURING)
    assert m is not None and m.kg == 117.2 and not m.final
    f = parse_eufy(FINAL)
    assert f is not None and f.kg == 110.6 and f.final
    o = parse_eufy(OFF)
    assert o is not None and o.kg == 0 and o.stepped_off and not o.final
    assert parse_eufy(b"\x01\x02") is None


def test_parse_xiaomi() -> None:
    # v2: ctrl0=0x02 (kg), ctrl1=0x24 (stabilised), date, impedance 500, weight 72.5 kg (14500 / 200)
    v2 = bytes([0x02, 0x24]) + (2026).to_bytes(2, "little") + bytes([11, 8, 9, 30, 0]) + (500).to_bytes(2, "little") + (14500).to_bytes(2, "little")
    r = parse_xiaomi_v2(v2)
    assert r is not None and r.kg == 72.5 and r.final
    removed = bytes([0x02, 0xA4]) + v2[2:]
    assert not parse_xiaomi_v2(removed).final  # type: ignore[union-attr]
    # v1: ctrl 0x22 (stabilised, kg), weight 80 kg
    r1 = parse_xiaomi_v1(bytes([0x22]) + (16000).to_bytes(2, "little") + bytes(7))
    assert r1 is not None and r1.kg == 80 and r1.final


def _feed(client: TestClient, address: str, name: str, payload: bytes, now: datetime | None = None, svc: dict | None = None) -> None:
    service = client.app.state.scale_service

    async def run() -> None:
        service.on_advertisement(address, name, {48223: payload} if payload else {}, svc or {}, now)
        await service.flush()

    client.portal.call(run)


def _discover_with(client: TestClient, adverts: list[tuple[str, str, dict, dict]]) -> list[dict]:
    service = client.app.state.scale_service

    async def fake_scan(cb):  # type: ignore[no-untyped-def]
        for a in adverts:
            cb(*a)
        await asyncio.Event().wait()

    service._scan = fake_scan
    return client.post("/api/scale/discover", json={"timeout_s": 2}, headers=AUTH).json()


def test_discover_add_weigh_rename_remove(client: TestClient) -> None:
    found = _discover_with(client, [
        ("AA:BB:CC:DD:EE:FF", "Headphones", {1: b"\x00" * 20}, {}),  # not a scale
        (ADDR, NAME, {48223: MEASURING}, {}),
    ])
    assert [(f["address"], f["model"], f["kg"]) for f in found] == [(ADDR, "eufy_c20", 117.2)]
    assert client.get("/api/scale", headers=AUTH).json()["bluetooth"] == "off"  # stops after searching

    # weighings of a scale that is not added are ignored
    t0 = datetime.now(timezone.utc)
    _feed(client, ADDR, NAME, FINAL, t0)
    assert client.get("/api/scale/weighings", headers=AUTH).json() == []

    status = client.post("/api/scale/scales", json={"address": ADDR}, headers=AUTH).json()
    assert [s["name"] for s in status["scales"]] == [NAME]
    assert status["bluetooth"] in ("listening", "off")
    assert _discover_with(client, [(ADDR, NAME, {48223: MEASURING}, {})]) == []  # already added

    _feed(client, ADDR, NAME, FINAL, t0 + timedelta(minutes=5))
    _feed(client, ADDR, NAME, FINAL, t0 + timedelta(minutes=5, seconds=1))  # same weighing
    rows = client.get("/api/scale/weighings?days=30", headers=AUTH).json()
    assert [r["kg"] for r in rows] == [110.6]

    s = client.patch(f"/api/scale/scales/{ADDR}", json={"name": "Μπάνιο"}, headers=AUTH).json()
    assert s["scales"][0]["name"] == "Μπάνιο" and s["scales"][0]["last_kg"] == 110.6

    assert client.delete(f"/api/scale/weighings/{rows[0]['id']}", headers=AUTH).status_code == 204
    assert client.get("/api/scale", headers=AUTH).json()["latest"] is None
    assert client.delete(f"/api/scale/weighings/{rows[0]['id']}", headers=AUTH).status_code == 404

    assert client.delete(f"/api/scale/scales/{ADDR}", headers=AUTH).json()["scales"] == []
    assert client.post("/api/scale/scales", json={"address": "11:22:33:44:55:66"}, headers=AUTH).status_code == 404


def test_xiaomi_discovered_by_service_data(client: TestClient) -> None:
    v2 = bytes([0x02, 0x04]) + bytes(9) + (14500).to_bytes(2, "little")
    found = _discover_with(client, [("C8:47:8C:00:00:01", "MIBFS", {}, {XIAOMI_V2_UUID: v2})])
    assert found[0]["model"] == "xiaomi_body2" and found[0]["kg"] == 72.5


def test_old_single_scale_setting_is_migrated(client: TestClient) -> None:
    service = client.app.state.scale_service

    async def run() -> None:
        await service._settings_repo.set("scale", {"enabled": True, "address": ADDR, "name": NAME})
        await service.start()
        await service.stop()

    client.portal.call(run)
    assert [s.address for s in service.status().scales] == [ADDR]


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
