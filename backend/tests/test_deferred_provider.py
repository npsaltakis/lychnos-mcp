from __future__ import annotations

import asyncio

from app.domain.models import DeviceState
from app.providers.registry import DeferredProvider
from tests.fakes import FakeLight, FakeProvider


class Sink:
    async def state_reported(self, provider, native_id, state: DeviceState, *, partial=False) -> None:
        pass

    async def address_changed(self, provider, native_id, address) -> None:
        pass


async def test_provider_that_fails_to_load_is_retried_until_it_loads() -> None:
    attempts = 0

    def factory():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ImportError("DLL load failed: an Application Control policy has blocked this file")
        return FakeProvider([FakeLight(native_id="a", name="A")])

    provider = DeferredProvider("fake", factory, retry_s=0.01)
    await provider.start(Sink())  # does not raise
    assert not provider.loaded
    assert await provider.discover(1) == []
    for _ in range(100):
        if provider.loaded:
            break
        await asyncio.sleep(0.01)
    assert provider.loaded and attempts == 3
    assert provider.manufacturer == "FakeCo"
    assert [d.native_id for d in await provider.discover(1)] == ["a"]
    await provider.stop()
