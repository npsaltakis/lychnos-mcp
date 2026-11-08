"""Presence: phones on the Wi-Fi decide whether anyone is home; leaving/arriving runs actions."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.services.presence_service import Neighbor, normalize_mac
from tests.conftest import AUTH, register_all

PHONE = "AA-BB-CC-11-22-33"


class FakeScanner:
    def __init__(self) -> None:
        self.present = True
        self.probed: list[list[str]] = []

    async def ping(self, ip: str) -> bool:
        return self.present and ip == "192.168.1.50"

    async def probe(self, ips: list[str]) -> None:
        self.probed.append(ips)

    async def neighbors(self) -> list[Neighbor]:
        state = "reachable" if self.present else "probe"  # an unanswered lookup is not presence
        return [Neighbor(ip="192.168.1.50", mac="aa:bb:cc:11:22:33", state=state),
                Neighbor(ip="192.168.1.1", mac="f0:0c:51:da:ec:7c", state="reachable")]

    def subnet_ips(self) -> list[str]:
        return [f"192.168.1.{i}" for i in range(1, 255)]


def _power(client: TestClient, device_id: str) -> bool | None:
    return client.get(f"/api/devices/{device_id}", headers=AUTH).json()["runtime"]["state"]["power"]


def test_mac_normalization_and_validation(client: TestClient) -> None:
    assert normalize_mac("AA-BB-CC-11-22-33") == "aa:bb:cc:11:22:33"
    body = {"enabled": True, "people": [{"name": "Νίκος", "mac": "nope"}]}
    assert client.put("/api/settings/presence", json=body, headers=AUTH).status_code == 422
    body["people"][0]["mac"] = PHONE
    status = client.put("/api/settings/presence", json=body, headers=AUTH).json()
    assert status["settings"]["people"][0]["mac"] == "aa:bb:cc:11:22:33"


def test_leave_switches_off_and_arrive_switches_on(client: TestClient) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    service = client.app.state.presence_service
    scanner = FakeScanner()
    service._scanner = scanner
    client.put("/api/settings/presence", json={
        "enabled": True, "people": [{"name": "Νίκος", "mac": PHONE}], "away_after_min": 10,
        "on_leave": "all_off", "on_arrive": "all_on", "arrive_only_dark": False,
    }, headers=AUTH)
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_power", "on": True}, headers=AUTH)
    t0 = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)

    client.portal.call(service.scan, t0)  # first reading: learn only
    assert service.status(t0).anyone_home is True and _power(client, desk) is True
    assert len(scanner.probed[0]) == 254  # first scan sweeps the subnet

    scanner.present = False
    client.portal.call(service.scan, t0 + timedelta(minutes=5))
    assert scanner.probed[-1] == ["192.168.1.50"]  # later scans poke only the phone
    assert _power(client, desk) is True  # not away yet
    client.portal.call(service.scan, t0 + timedelta(minutes=11))
    assert service.status(t0 + timedelta(minutes=11)).anyone_home is False
    assert _power(client, desk) is False

    scanner.present = True
    client.portal.call(service.scan, t0 + timedelta(minutes=30))
    assert _power(client, desk) is True
    log = client.get("/api/activity?limit=50", headers=AUTH).json()
    assert any(e["source"] == "presence" for e in log)


def test_since_tracks_arrival_and_leaving(client: TestClient) -> None:
    service = client.app.state.presence_service
    scanner = FakeScanner()
    service._scanner = scanner
    client.put("/api/settings/presence", json={
        "enabled": True, "people": [{"name": "Νίκος", "mac": PHONE}], "away_after_min": 10, "on_leave": "nothing",
    }, headers=AUTH)
    t0 = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)
    client.portal.call(service.scan, t0)
    client.portal.call(service.scan, t0 + timedelta(minutes=5))
    assert service.status().people[0].since == t0  # stays at arrival, not "now"
    scanner.present = False
    client.portal.call(service.scan, t0 + timedelta(minutes=20))
    person = service.status(t0 + timedelta(minutes=20)).people[0]
    assert not person.home and person.since == t0 + timedelta(minutes=5)  # left after last sighting
