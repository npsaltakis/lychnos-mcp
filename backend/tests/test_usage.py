"""Usage statistics: hours on per light per day, from on/off events."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.services.usage_service import on_seconds
from tests.conftest import AUTH, register_all

T0 = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)


def test_on_seconds_intervals() -> None:
    ev = [(T0, True), (T0 + timedelta(hours=2), False), (T0 + timedelta(hours=5), True)]
    assert on_seconds(ev, T0 - timedelta(hours=1), T0 + timedelta(hours=6)) == 3 * 3600
    # window starts while the light is on
    assert on_seconds(ev, T0 + timedelta(hours=1), T0 + timedelta(hours=3)) == 3600
    # last event before the window says "on"
    assert on_seconds([(T0, True)], T0 + timedelta(days=1), T0 + timedelta(days=1, hours=4)) == 4 * 3600
    assert on_seconds([], T0, T0 + timedelta(hours=1)) == 0


def test_usage_report_counts_power_changes(client: TestClient) -> None:
    ids = register_all(client)
    desk = ids["Desk Bar"]
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_power", "on": False}, headers=AUTH)
    client.post(f"/api/devices/{desk}/commands", json={"type": "set_power", "on": True}, headers=AUTH)
    report = client.get("/api/stats/usage?days=7", headers=AUTH).json()
    assert len(report["days"]) == 7
    row = next(d for d in report["devices"] if d["device_id"] == desk)
    assert len(row["hours"]) == 7 and row["hours"][-1] >= 0
    events = client.portal.call(
        client.app.state.usage_service._repo.between,
        datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert events[desk][-1][1] is True
