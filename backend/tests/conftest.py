from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.secrets import MemorySecretStore
from app.main import create_app
from tests.fakes import POWER_ONLY_CAPS, FakeLight, FakeProvider

TOKEN = "test-token-0123456789abcdefghijklmnopqrstuvwxyz"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        api_token=TOKEN,
        allowed_hosts=["testserver"],
        log_to_file=False,
        state_max_age_s=60,
        poll_interval_s=3600,
        offline_after_failures=2,
        command_timeout_s=0.5,
        frontend_dist=tmp_path / "no-dashboard",
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        [
            FakeLight("aa:01", "Desk Bar"),
            FakeLight("bb:02", "Plug Lamp", caps=POWER_ONLY_CAPS, report_state=False),
        ]
    )


@pytest.fixture
def client(settings: Settings, provider: FakeProvider) -> Iterator[TestClient]:
    app = create_app(settings, providers=[provider], secret_store=MemorySecretStore(), weather_enabled=False)
    with TestClient(app) as test_client:
        yield test_client


def register_all(client: TestClient) -> dict[str, str]:
    """Discover + register every fake device; returns name -> device id."""
    found = client.post("/api/discovery/scan", headers=AUTH).json()
    ids = {}
    for view in found:
        device_id = view["device"]["id"]
        client.patch(f"/api/devices/{device_id}", json={"registered": True}, headers=AUTH)
        ids[view["device"]["name"]] = device_id
    return ids
