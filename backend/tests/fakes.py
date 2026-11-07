"""Test-only fake provider. Never imported by the application itself."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.errors import DeviceOfflineError
from app.domain.models import (
    RGB,
    Capabilities,
    Capability,
    ColorTempRange,
    Command,
    DeviceRecord,
    DeviceState,
    DiscoveredDevice,
    LightMode,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
)
from app.providers.base import DeviceProvider, ProviderEventSink

FULL_CAPS = Capabilities(
    supported=frozenset(Capability),
    color_temp_range=ColorTempRange(min_k=2000, max_k=9000),
    supports_state_query=True,
)
POWER_ONLY_CAPS = Capabilities(supported=frozenset({Capability.POWER}), supports_state_query=False)


@dataclass
class FakeLight:
    native_id: str
    name: str
    caps: Capabilities = FULL_CAPS
    state: DeviceState = field(
        default_factory=lambda: DeviceState(power=False, brightness=50, color=RGB(r=255, g=255, b=255), mode=LightMode.COLOR)
    )
    online: bool = True
    report_state: bool = True  # False -> behaves like a device without read-back
    ignore_commands: bool = False  # True -> device accepts but does not change (mismatch)
    delay_s: float = 0.0


class FakeProvider(DeviceProvider):
    key = "fake"
    manufacturer = "FakeCo"

    def __init__(self, lights: list[FakeLight]) -> None:
        self.lights = {light.native_id: light for light in lights}
        self.get_state_calls = 0
        self.sink: ProviderEventSink | None = None

    async def start(self, sink: ProviderEventSink) -> None:
        self.sink = sink

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        return [
            DiscoveredDevice(
                provider=self.key,
                native_id=light.native_id,
                name=light.name,
                manufacturer=self.manufacturer,
                model="FK-1",
                address="10.0.0.1",
                capabilities=light.caps,
            )
            for light in self.lights.values()
            if light.online
        ]

    def _light(self, device: DeviceRecord) -> FakeLight:
        light = self.lights[device.native_id]
        if not light.online:
            raise DeviceOfflineError(f"{device.name} is offline")
        return light

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        self.get_state_calls += 1
        light = self._light(device)
        await asyncio.sleep(light.delay_s)
        return light.state

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        light = self._light(device)
        await asyncio.sleep(light.delay_s)
        if not light.ignore_commands:
            match command:
                case SetPower(on=on):
                    light.state = light.state.model_copy(update={"power": on})
                case SetBrightness(brightness=b):
                    light.state = light.state.model_copy(update={"brightness": b})
                case SetColor(color=c):
                    light.state = light.state.model_copy(update={"color": c, "mode": LightMode.COLOR})
                case SetColorTemperature(kelvin=k):
                    light.state = light.state.model_copy(update={"color_temp_k": k, "mode": LightMode.WHITE})
        return light.state if light.report_state else None
